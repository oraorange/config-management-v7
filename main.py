#!/usr/bin/env python3
"""
Инструмент визуализации графа зависимостей для APK (Alpine Linux)
Вариант №7
"""

import configparser
import sys
import os
import requests
import tarfile
import tempfile
import re
from typing import List, Dict, Set, Optional
from io import BytesIO
from collections import defaultdict, deque

# ========== Этап 1: Чтение конфигурации с обработкой ошибок ==========
def read_config(file_path: str) -> Dict:
    """Чтение INI-файла и вывод параметров с обработкой ошибок."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Конфигурационный файл {file_path} не найден")
    
    config = configparser.ConfigParser()
    try:
        config.read(file_path)
    except configparser.Error as e:
        raise ValueError(f"Ошибка парсинга конфига: {e}")
    
    if 'settings' not in config:
        raise ValueError("Нет секции [settings] в конфиге")
    
    params = dict(config['settings'])
    
    # Проверка обязательных параметров
    required = ['package_name', 'repository_url']
    for param in required:
        if param not in params or not params[param]:
            raise ValueError(f"Не указан обязательный параметр: {param}")
    
    # Преобразование test_mode в bool
    if 'test_mode' in params:
        params['test_mode'] = params['test_mode'].lower() == 'true'
    else:
        params['test_mode'] = False
    
    # Вывод параметров (только для этапа 1)
    print("=== Параметры конфигурации ===")
    for key, value in params.items():
        print(f"{key} = {value}")
    
    return params

# ========== Этап 2: Сбор данных ==========
def download_apkindex(repo_url: str) -> Optional[Dict[str, List[str]]]:
    """Скачивание и парсинг APKINDEX.tar.gz."""
    try:
        apkindex_url = f"{repo_url}/x86_64/APKINDEX.tar.gz"
        print(f"Скачивание APKINDEX из {apkindex_url}")
        
        response = requests.get(apkindex_url, timeout=10)
        response.raise_for_status()
        
        # Распаковка tar.gz в памяти
        with tarfile.open(fileobj=BytesIO(response.content), mode='r:gz') as tar:
            apkindex_content = tar.extractfile('APKINDEX').read().decode('utf-8')
        
        # Парсинг APKINDEX
        packages = {}
        current_pkg = None
        
        for line in apkindex_content.split('\n'):
            if line.startswith('P:'):  # Package name
                current_pkg = line[2:]
                packages[current_pkg] = []
            elif line.startswith('D:'):  # Dependencies
                if current_pkg:
                    # Убираем версии из зависимостей: libc.so.6=>1.2.3 → libc.so.6
                    deps = re.findall(r'([\w\.\-]+)(?:[=<>!].*?)?', line[2:])
                    packages[current_pkg].extend([d for d in deps if d])
        
        return packages
        
    except Exception as e:
        print(f"Ошибка при загрузке APKINDEX: {e}")
        return None

def fetch_apk_dependencies(package: str, version: str, repo_url: str,
                          test_mode: bool, test_repo_path: str) -> List[str]:
    """Получение зависимостей пакета APK."""
    
    if test_mode:
        # Режим тестирования: читаем из файла
        print(f"Тестовый режим: чтение из {test_repo_path}")
        return get_test_dependencies(package, test_repo_path)
    
    # Реальный режим: парсим APKINDEX
    print(f"Запрос зависимостей для {package} {version} из {repo_url}")
    
    # Кэшируем APKINDEX, чтобы не скачивать каждый раз
    if not hasattr(fetch_apk_dependencies, 'apkindex_cache'):
        fetch_apk_dependencies.apkindex_cache = download_apkindex(repo_url)
    
    apkindex = fetch_apk_dependencies.apkindex_cache
    
    if apkindex and package in apkindex:
        deps = apkindex[package]
        print(f"Найдены зависимости для {package}: {deps}")
        return deps
    else:
        print(f"Пакет {package} не найден в репозитории")
        return []

def get_test_dependencies(package: str, test_file: str) -> List[str]:
    """Чтение зависимостей из тестового файла (формат: A:B,C)."""
    if not os.path.exists(test_file):
        print(f"Тестовый файл {test_file} не найден")
        return []
    
    try:
        with open(test_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or ':' not in line:
                    continue
                pkg, deps_str = line.split(':', 1)
                if pkg == package:
                    return [d.strip() for d in deps_str.split(',') if d.strip()]
        return []
    except Exception as e:
        print(f"Ошибка чтения тестового файла: {e}")
        return []

# ========== Этап 3: Построение графа (DFS с рекурсией) ==========
def build_dependency_graph(package: str, version: str, repo_url: str,
                          test_mode: bool, test_repo_path: str,
                          visited: Set = None, path: Set = None,
                          graph: Dict = None, depth: int = 0) -> Dict[str, List[str]]:
    """Рекурсивный DFS для построения графа зависимостей с обработкой циклов."""
    
    if visited is None:
        visited = set()
    if path is None:
        path = set()
    if graph is None:
        graph = {}
    
    # Обнаружение циклической зависимости
    if package in path:
        print(f"⚠️ Обнаружена циклическая зависимость: {package}")
        graph[package] = []
        return graph
    
    if package in visited:
        return graph
    
    visited.add(package)
    path.add(package)
    
    # Получаем зависимости
    deps = fetch_apk_dependencies(package, version, repo_url, test_mode, test_repo_path)
    graph[package] = deps
    
    # Рекурсивно обрабатываем зависимости
    for dep in deps:
        build_dependency_graph(dep, version, repo_url, test_mode, test_repo_path,
                              visited, path, graph, depth + 1)
    
    path.remove(package)
    return graph

# ========== Этап 4: Обратные зависимости ==========
def find_reverse_dependencies(graph: Dict[str, List[str]], target: str) -> List[str]:
    """Поиск пакетов, которые зависят от target."""
    reverse = []
    for pkg, deps in graph.items():
        if target in deps:
            reverse.append(pkg)
    
    # (только для этого этапа) Выводим результат
    print(f"\n=== Обратные зависимости для {target} ===")
    if reverse:
        for pkg in reverse:
            print(f"  - {pkg}")
    else:
        print("  (нет обратных зависимостей)")
    
    return reverse

# ========== Этап 5: Визуализация PlantUML ==========
def generate_plantuml(graph: Dict[str, List[str]], output_file: str):
    """Генерация PlantUML-кода и сохранение в файл."""
    
    # Генерируем уникальные цвета для разных уровней вложенности
    colors = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FFEAA7']
    
    # Находим все пакеты и их уровни (BFS)
    levels = {}
    queue = deque()
    
    # Начинаем с корневого пакета (первый в графе)
    root = list(graph.keys())[0] if graph else ""
    if root:
        queue.append((root, 0))
        levels[root] = 0
        
        while queue:
            pkg, level = queue.popleft()
            for dep in graph.get(pkg, []):
                if dep not in levels:
                    levels[dep] = level + 1
                    queue.append((dep, level + 1))
    
    # Генерация PlantUML
    puml = """@startuml
!define RECTANGLE class
skinparam class {
    BackgroundColor White
    BorderColor Black
    ArrowColor #555555
    FontSize 13
}
hide empty members
left to right direction\n\n"""
    
    # Определяем цвета для пакетов
    for pkg in graph.keys():
        level = min(levels.get(pkg, 0), len(colors)-1)
        color = colors[level]
        puml += f'rectangle "{pkg}" as {pkg.replace("-", "_")} #{color}\n'
    
    puml += "\n"
    
    # Добавляем связи
    for pkg, deps in graph.items():
        for dep in deps:
            if dep in graph:  # Добавляем только если зависимость есть в графе
                puml += f'{pkg.replace("-", "_")} --> {dep.replace("-", "_")}\n'
    
    puml += "@enduml"
    
    # Сохраняем в файл
    with open(output_file, 'w') as f:
        f.write(puml)
    
    print(f"\n✅ PlantUML код сохранён в {output_file}")
    
    # Пробуем сгенерировать изображение (если установлен plantuml)
    try:
        os.system(f"plantuml {output_file}")
        if os.path.exists(output_file.replace('.puml', '.png')):
            print(f"✅ Изображение графа сохранено в {output_file.replace('.puml', '.png')}")
            print("\nДля просмотра изображения откройте файл или используйте команду:")
            print(f"open {output_file.replace('.puml', '.png')}")
    except:
        print("\nℹ️ Для генерации PNG установите PlantUML:")
        print("brew install plantuml")
        print("Или используйте онлайн версию: https://www.plantuml.com/plantuml/uml/")
    
    return puml

# ========== Основной поток ==========
def main():
    try:
        # Этап 1: Чтение конфигурации
        config = read_config('config.ini')
        
        pkg = config['package_name']
        ver = config.get('version', '')
        repo = config['repository_url']
        test_mode = config['test_mode']
        test_repo = config.get('test_repo_path', 'test_graph.txt')
        
        print(f"\n{'='*50}")
        print("Этап 2: Сбор данных")
        print('='*50)
        
        # Этап 2: Вывод прямых зависимостей
        deps = fetch_apk_dependencies(pkg, ver, repo, test_mode, test_repo)
        print(f"Прямые зависимости {pkg}: {deps}")
        
        print(f"\n{'='*50}")
        print("Этап 3: Построение графа зависимостей")
        print('='*50)
        
        # Этап 3: Построение полного графа
        print("Построение графа (DFS с рекурсией)...")
        graph = build_dependency_graph(pkg, ver, repo, test_mode, test_repo)
        
        print(f"\nПолный граф зависимостей:")
        for pkg_name, dependencies in graph.items():
            if dependencies:
                print(f"  {pkg_name} -> {', '.join(dependencies)}")
            else:
                print(f"  {pkg_name} -> (нет зависимостей)")
        
        print(f"\n{'='*50}")
        print("Этап 4: Дополнительные операции")
        print('='*50)
        
        # Этап 4: Поиск обратных зависимостей
        reverse_deps = find_reverse_dependencies(graph, pkg)
        
        print(f"\n{'='*50}")
        print("Этап 5: Визуализация")
        print('='*50)
        
        # Этап 5: Визуализация
        puml_file = "dependency_graph.puml"
        generate_plantuml(graph, puml_file)
        
        # Показываем пример PlantUML кода
        print(f"\nПример PlantUML кода (первые 10 строк):")
        with open(puml_file, 'r') as f:
            for i, line in enumerate(f):
                if i < 10:
                    print(f"  {line.rstrip()}")
                else:
                    print("  ...")
                    break
        
        print(f"\n{'='*50}")
        print("✅ Все этапы выполнены успешно!")
        print('='*50)
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}", file=sys.stderr)
        print("\nПроверьте:")
        print("1. Существует ли config.ini")
        print("2. Правильно ли указаны параметры")
        print("3. Есть ли доступ к интернету (для реального режима)")
        sys.exit(1)

if __name__ == "__main__":
    main()
