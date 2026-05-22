from PIL import Image

img = Image.open('/app/config/LOGO.png')
print(f'Size: {img.size}')
print(f'Mode: {img.mode}')
print(f'Has transparency: {img.mode=="RGBA" or "transparency" in img.info}')
