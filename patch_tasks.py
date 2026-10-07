file_path = 'frontend/tasks.js'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

idx = content.find('"plant",')
if idx != -1:
    content = content[:idx] + '"small plant",\n  ' + content[idx:]
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Patched tasks.js")
