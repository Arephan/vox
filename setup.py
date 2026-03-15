from setuptools import setup

APP = ['app.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': False,
    'iconfile': None,
    'plist': {
        'CFBundleName': 'Vox',
        'CFBundleDisplayName': 'Vox',
        'CFBundleIdentifier': 'com.arephan.vox',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'LSMinimumSystemVersion': '13.0',
        'LSUIElement': True,  # Menu bar app, no dock icon
        'NSMicrophoneUsageDescription': 'Vox needs microphone access for voice input.',
        'NSAccessibilityUsageDescription': 'Vox needs accessibility access for the global hotkey.',
    },
    'packages': [
        'rumps', 'sounddevice', 'soundfile', 'numpy',
        'faster_whisper', 'ctranslate2', 'pynput',
        'tokenizers', 'huggingface_hub',
    ],
    'includes': [
        'cffi', '_cffi_backend',
    ],
}

setup(
    app=APP,
    name='Vox',
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
