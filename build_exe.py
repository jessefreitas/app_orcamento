import PyInstaller.__main__

PyInstaller.__main__.run([
    '--name=AppOrcamento',
    '--onefile',
    '--windowed',
    'main.py',
])
