with open('/app/templates/upload.html', 'r') as f:
    content = f.read()
    if 'LOGO_transparent.png' in content:
        print('SUCCESS: Page is using transparent logo')
    else:
        print('FAILED: Page is not using transparent logo')
