with open('/app/templates/upload.html', 'r') as f:
    content = f.read()
    # 找到hero-strip的样式定义
    start = content.find('.hero-strip {')
    if start != -1:
        end = content.find('}', start)
        print(content[start:end+1])
