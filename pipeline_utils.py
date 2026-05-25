"""Shared infrastructure for the local project data pipeline."""

import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd


CONFIG_FILE = "pipeline_config.json"
_BACKED_UP_THIS_RUN = set()


class SafeFormatDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def resolve_config_value(value, paths):
    if isinstance(value, str):
        return value.format_map(SafeFormatDict(paths))

    if isinstance(value, list):
        return [resolve_config_value(item, paths) for item in value]

    if isinstance(value, dict):
        return {
            key: resolve_config_value(item, paths)
            for key, item in value.items()
        }

    return value


def build_config_placeholders(config):
    placeholders = dict(config.get("paths", {}))
    step1_config = config.get("step1", {})
    row_start = step1_config.get("row_start")
    row_end = step1_config.get("row_end")

    if row_start is not None and row_end is not None:
        placeholders["batch_range"] = f"{row_start}_{row_end}"

    return placeholders


def load_pipeline_config(config_file=CONFIG_FILE):
    path = Path(config_file)
    if not path.exists():
        return {}

    with open(path, "r", encoding="utf-8") as input_file:
        config = json.load(input_file)

    placeholders = build_config_placeholders(config)
    return resolve_config_value(config, placeholders)


def get_config_section(section_name):
    return load_pipeline_config().get(section_name, {})


def require_config_section(section_name):
    section = get_config_section(section_name)
    if not section:
        raise KeyError(f"Missing required config section: {section_name}")
    return section


def require_config_value(section, key, section_name):
    if key not in section:
        raise KeyError(f"Missing required config value: {section_name}.{key}")
    return section[key]


def get_safety_config():
    return load_pipeline_config().get("safety", {})


class TeeStream:
    def __init__(self, original_stream, logger, level):
        self.original_stream = original_stream
        self.logger = logger
        self.level = level
        self._buffer = ""

    def write(self, message):
        self.original_stream.write(message)
        self.original_stream.flush()

        self._buffer += message
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self.logger.log(self.level, line)

    def flush(self):
        self.original_stream.flush()
        if self._buffer.strip():
            self.logger.log(self.level, self._buffer.strip())
        self._buffer = ""


def setup_logging(step_name):
    config = load_pipeline_config()
    logging_config = config.get("logging", {})
    log_dir = Path(logging_config.get("log_dir", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    level_name = str(logging_config.get("level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger(step_name)
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    log_file = log_dir / f"{step_name}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    logger.addHandler(file_handler)

    if not isinstance(sys.stdout, TeeStream):
        sys.stdout = TeeStream(sys.__stdout__, logger, logging.INFO)
    if not isinstance(sys.stderr, TeeStream):
        sys.stderr = TeeStream(sys.__stderr__, logger, logging.ERROR)

    logger.info("Logging started for %s", step_name)
    return logger


def validate_columns(df, required_columns, context):
    missing = [column for column in required_columns if column not in df.columns]
    if missing:
        raise ValueError(
            f"{context} is missing required columns: {', '.join(missing)}"
        )


def validate_records(records, required_keys, context):
    for index, record in enumerate(records, start=1):
        missing = [key for key in required_keys if key not in record]
        if missing:
            raise ValueError(
                f"{context} record {index} is missing keys: {', '.join(missing)}"
            )


def timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def backup_existing_file(path, enabled=None):
    path = Path(path)
    if enabled is None:
        enabled = bool(get_safety_config().get("backup_existing_outputs", True))

    if not enabled or not path.exists():
        return None

    resolved_path = str(path.resolve())
    if resolved_path in _BACKED_UP_THIS_RUN:
        return None

    backup_dir = path.parent / "_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{path.stem}_{timestamp()}{path.suffix}"
    shutil.copy2(path, backup_path)
    _BACKED_UP_THIS_RUN.add(resolved_path)
    return backup_path


def ensure_parent_dir(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_excel_safely(df, output_path, index=False, backup=True):
    output_path = ensure_parent_dir(output_path)
    backup_existing_file(output_path, enabled=backup)
    df.to_excel(output_path, index=index)


def write_csv_safely(df, output_path, index=False, encoding="utf-8-sig", backup=True):
    output_path = ensure_parent_dir(output_path)
    backup_existing_file(output_path, enabled=backup)
    df.to_csv(output_path, index=index, encoding=encoding)


def write_text_safely(output_path, text, encoding="utf-8", backup=True):
    output_path = ensure_parent_dir(output_path)
    backup_existing_file(output_path, enabled=backup)
    output_path.write_text(text, encoding=encoding)


def iter_files_safely(root):
    root = Path(root)
    try:
        iterator = root.rglob("*")
        for file_path in iterator:
            try:
                if file_path.is_file():
                    yield file_path
            except OSError as exc:
                print(f"Warning: cannot inspect path {file_path}: {exc}")
    except OSError as exc:
        print(f"Warning: cannot scan folder {root}: {exc}")


def copy_file_safely(source_file, destination):
    source_file = Path(source_file)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    temp_destination = destination.with_name(
        f"{destination.name}.tmp_copy_{timestamp()}"
    )

    shutil.copy2(source_file, temp_destination)
    temp_destination.replace(destination)
    return destination
