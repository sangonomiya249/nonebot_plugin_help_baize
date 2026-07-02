import re
from pathlib import Path
p = re.compile(r'\u([0-9a-fA-F]{4})')
for name in ['__init__.py']:
    f = Path(name)
    if not f.exists():
        continue
    text = f.read_text(encoding='utf-8')
    new_text = p.sub(lambda m: chr(int(m.group(1), 16)), text)
    if new_text != text:
        f.write_text(new_text, encoding='utf-8')
        print(f'OK: {name}')
    else:
        print(f'SKIP: {name}')
