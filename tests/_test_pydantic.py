from importlib.metadata import files
print([file for file in files('pydantic-core') if file.name.startswith('_pydantic_core')])