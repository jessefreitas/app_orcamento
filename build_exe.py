import PyInstaller.__main__
import os
import shutil

APP_NAME = "AppOrcamento"
SCRIPT = "main.py"
ICON = "assets/icon.ico"  # Placeholder, we'll create this later

def build(pyinstaller_args):
    """Builds the executable using PyInstaller."""
    print(f"Running PyInstaller with args: {pyinstaller_args}")
    PyInstaller.__main__.run(pyinstaller_args)

    print("\nBuild finished.")
    print(f"Executable is in the 'dist' folder.")

if __name__ == '__main__':
    if not os.path.exists('assets'):
        os.makedirs('assets')
    
    pyinstaller_args = [
        '--name=%s' % APP_NAME,
        '--onefile',
        '--windowed',
        '--add-data=assets;assets',
        SCRIPT,
    ]

    if os.path.exists(ICON):
        pyinstaller_args.append(f'--icon={ICON}')
    else:
        print(f"Warning: Icon file '{ICON}' not found. Using default icon.")

    build(pyinstaller_args)

    # Clean up build files
    print("\nCleaning up build files...")
    if os.path.exists('build'):
        shutil.rmtree('build')
    if os.path.exists(f'{APP_NAME}.spec'):
        os.remove(f'{APP_NAME}.spec')
    print("Cleanup complete.")
