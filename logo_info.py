from PIL import Image

img = Image.open('/app/config/LOGO.png')
print(f'尺寸: {img.size}')
print(f'模式: {img.mode}')
print(f'格式: {img.format}')
print(f'大小: {len(open("/app/config/LOGO.png","rb").read())/1024:.1f} KB')
