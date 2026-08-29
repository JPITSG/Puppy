#!/bin/bash
set -e

script_path=$(readlink -f -- "${BASH_SOURCE[0]}")
project_dir=$(dirname -- "$script_path")
cd -- "$project_dir"
exec "${PUPPY_PYTHON:-python3}" -m puppy
