from PIL import Image, ImageDraw
import numpy as np

def make_white_background_transparent(image_path, output_path, tolerance=30):
    """
    将白色背景转换为透明

    Args:
        image_path: 输入图片路径
        output_path: 输出图片路径
        tolerance: 白色容差值（0-255），值越大透明区域越多
    """
    # 打开图片
    img = Image.open(image_path)

    # 如果不是RGBA模式，转换为RGBA
    if img.mode != 'RGBA':
        img = img.convert('RGBA')

    # 转换为numpy数组
    img_array = np.array(img)

    # 定义白色范围（R, G, B 都接近255）
    # 使用容差值来确定什么是"白色"
    white_lower = 255 - tolerance
    white_mask = (
        (img_array[:, :, 0] >= white_lower) &
        (img_array[:, :, 1] >= white_lower) &
        (img_array[:, :, 2] >= white_lower)
    )

    # 将白色像素的Alpha通道设为0（透明）
    img_array[white_mask, 3] = 0

    # 转换回PIL Image
    transparent_img = Image.fromarray(img_array, 'RGBA')

    # 保存为PNG
    transparent_img.save(output_path, 'PNG', optimize=True)

    print(f"✓ 已处理图片: {image_path}")
    print(f"✓ 保存到: {output_path}")
    print(f"✓ 容差值: {tolerance}")
    print(f"✓ 尺寸: {transparent_img.size}")
    print(f"✓ 模式: {transparent_img.mode}")

# 处理Logo
make_white_background_transparent(
    '/app/config/LOGO.png',
    '/app/config/LOGO_transparent.png',
    tolerance=30  # 容差值，可以根据需要调整
)
