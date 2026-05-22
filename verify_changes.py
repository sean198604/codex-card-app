with open('/app/templates/upload.html', 'r') as f:
    content = f.read()

# 验证1: 深色背景
if 'linear-gradient(135deg, var(--hero-bg-start) 0%, var(--hero-bg-end) 100%)' in content:
    print('✓ 深色背景已恢复')
else:
    print('✗ 深色背景未恢复')

# 验证2: sticky定位
if 'position: sticky' in content and 'top: 0' in content:
    print('✓ Sticky定位已设置（冻结窗口效果）')
else:
    print('✗ Sticky定位未设置')

# 验证3: Logo位置和透明效果
if 'top: -10px' in content and 'right: 10px' in content:
    print('✓ Logo位置已调整（右上角）')
else:
    print('✗ Logo位置未调整')

if 'mix-blend-mode' in content:
    print('✓ Logo透明混合模式已设置')
else:
    print('✗ Logo透明混合模式未设置')
