import os
import subprocess

def run_skill(args, player=None):
    desktop_path = os.path.join(os.path.expanduser('~'), 'Desktop')
    shortcut_path = os.path.join(desktop_path, 'Запустить EDIT.bat')
    
    # Assuming 'launch_edit.exe' or similar command exists to restart/launch the assistant
    # This is a placeholder command
    launch_command = "start EDIT.exe" 

    try:
        with open(shortcut_path, 'w', encoding='cp866') as f:
            f.write(f"@echo off\n{launch_command}\nexit")
        return f"Ярлык 'Запустить EDIT.bat' создан на рабочем столе. Вы можете использовать его для быстрого запуска."
    except Exception as e:
        return f"Не удалось создать ярлык: {e}"