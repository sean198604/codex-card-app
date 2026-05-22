from PIL import Image
import numpy as np

# 打开原始Logo
img = Image.open('/app/config/LOGO.png')

# 如果是RGBA模式，增强透明度
if img.mode == 'RGBA':
    # 获取Alpha通道
    alpha = img.split()[3]

    # 可以选择性地调整透明度
    # 例如：让半透明像素更透明
    alpha = alpha.point(lambda x: max(0, min(255, int(x * 0.9))))

    # 重新组合通道
    img.putalpha(alpha)

# 保存为优化的透明PNG
img.save('/app/config/LOGO_transparent.png', 'PNG', optimize=True)
print('Logo converted to transparent PNG successfully')
print(f'Saved as: /app/config/LOGO_transparent.png')
print(f'Size: {img.size}, Mode: {img.mode}')
