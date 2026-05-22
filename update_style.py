import re

# 读取文件
with open('/app/templates/upload.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 替换样式
content = content.replace(
    'background: linear-gradient(135deg, var(--hero-bg-start) 0%, var(--hero-bg-end) 100%);',
    'background: var(--card-bg);'
)
content = content.replace('color: white', 'color: var(--text-main)')

# 写回文件
with open('/app/templates/upload.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('File updated successfully')
