file_path = 'frontend/index.html'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

new_label = '''              <label>
                <input type="radio" name="itemType" value="small_plant">
                <span><strong>Small Plant</strong><small>Size S, no measurements</small></span>
              </label>\n'''

idx = content.find('<input type="radio" name="itemType" value="plant">')
if idx != -1:
    label_end = content.find('</label>', idx) + 8
    content = content[:label_end] + '\n' + new_label + content[label_end:]
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Patched index.html")
