import os
import sys
import paramiko

def run_ssh_cmd(ssh, cmd):
    print(f"Ejecutando comando remoto: {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if out:
        print("Salida:", out)
    if err:
        print("Error/Aviso:", err)
    return out, err

def main():
    ip = "100.104.159.51"
    user = "aj"
    pw = "101978"
    
    local_dir = "/Users/alejandro/.gemini/antigravity-ide/scratch/abastur_scraper"
    gcp_key_local = "/Users/alejandro/Viajes RM/pivotal-keep-447418-r0-dca467a9d1b3.json"
    
    remote_dir = r"C:\Users\aj\abastur_scraper"
    
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        print(f"Conectando a {user}@{ip}...")
        ssh.connect(ip, username=user, password=pw)
        print("¡Conectado con éxito!")
    except Exception as e:
        print("Error de conexión SSH:", e)
        sys.exit(1)
        
    # 1. Crear carpeta en la máquina Windows
    run_ssh_cmd(ssh, f'powershell -Command "New-Item -ItemType Directory -Force -Path {remote_dir}"')
    
    # 2. Transferir archivos usando SFTP
    print("Iniciando transferencia de archivos SFTP...")
    sftp = ssh.open_sftp()
    
    files_to_copy = {
        "scraper.py": os.path.join(local_dir, "scraper.py"),
        "upload_to_sheets.py": os.path.join(local_dir, "upload_to_sheets.py"),
        "requirements.txt": os.path.join(local_dir, "requirements.txt"),
        "run_local.bat": os.path.join(local_dir, "run_local.bat"),
        "gcp_key.json": gcp_key_local
    }
    
    for remote_name, local_path in files_to_copy.items():
        if os.path.exists(local_path):
            remote_path = remote_dir + "\\" + remote_name
            print(f"Copiando {local_path} -> {remote_path}")
            sftp.put(local_path, remote_path)
        else:
            print(f"Advertencia: No se encontró el archivo local {local_path}")
            
    sftp.close()
    print("Transferencia de archivos completada.")
    
    # 3. Configurar entorno de Python en Windows
    print("Configurando entorno virtual de Python en Windows...")
    # Verificar si python está instalado
    out, err = run_ssh_cmd(ssh, "python --version")
    if "python" not in out.lower() and "python" not in err.lower():
        print("Error: Python no parece estar instalado o no está en el PATH de la máquina de Windows.")
        print("Por favor, asegúrate de instalar Python en Windows y marcar la opción 'Add Python to PATH'.")
        ssh.close()
        sys.exit(1)
        
    # Crear venv
    run_ssh_cmd(ssh, f"cd {remote_dir} && python -m venv venv")
    
    # Instalar requerimientos
    print("Instalando dependencias en Windows (esto puede tardar un minuto)...")
    run_ssh_cmd(ssh, f"cd {remote_dir} && venv\\Scripts\\pip install -r requirements.txt")
    
    # Instalar Playwright Chromium
    print("Instalando navegadores de Playwright en Windows...")
    run_ssh_cmd(ssh, f"cd {remote_dir} && venv\\Scripts\\playwright install chromium")
    
    # 4. Crear tarea programada en Windows Task Scheduler
    print("Configurando tarea programada en Windows (cada hora)...")
    # Borrar si ya existe para evitar errores
    run_ssh_cmd(ssh, 'schtasks /delete /f /tn "AbasturScraper"')
    
    # Crear tarea horaria
    task_cmd = f'schtasks /create /f /tn "AbasturScraper" /tr "cmd.exe /c {remote_dir}\\run_local.bat" /sc hourly /mo 1 /ru {user} /rp {pw}'
    run_ssh_cmd(ssh, task_cmd)
    
    print("\n¡Configuración completada con éxito!")
    print("El scraper ahora correrá cada hora en la máquina Windows.")
    ssh.close()

if __name__ == "__main__":
    main()
