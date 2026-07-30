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
    
    remote_dir = "/home/aj/abastur_scraper"
    
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        print(f"Conectando a {user}@{ip} (Linux)...")
        ssh.connect(ip, username=user, password=pw)
        print("¡Conectado con éxito!")
    except Exception as e:
        print("Error de conexión SSH:", e)
        sys.exit(1)
        
    # 0. Limpiar archivos mal nombrados del intento anterior en la carpeta raíz
    print("Limpiando archivos mal nombrados del intento de Windows...")
    run_ssh_cmd(ssh, "rm -f 'C:\\Users\\aj\\abastur_scraper'* 'C:\\Users\\aj\\abastur_scraper'/*")
    
    # 1. Crear carpeta en la máquina Linux
    run_ssh_cmd(ssh, f"mkdir -p {remote_dir}")
    
    # 2. Transferir archivos usando SFTP
    print("Iniciando transferencia de archivos SFTP...")
    sftp = ssh.open_sftp()
    
    files_to_copy = {
        "scraper.py": os.path.join(local_dir, "scraper.py"),
        "upload_to_sheets.py": os.path.join(local_dir, "upload_to_sheets.py"),
        "requirements.txt": os.path.join(local_dir, "requirements.txt"),
        "run_local.sh": os.path.join(local_dir, "run_local.sh"),
        "gcp_key.json": gcp_key_local
    }
    
    for remote_name, local_path in files_to_copy.items():
        if os.path.exists(local_path):
            remote_path = f"{remote_dir}/{remote_name}"
            print(f"Copiando {local_path} -> {remote_path}")
            sftp.put(local_path, remote_path)
        else:
            print(f"Advertencia: No se encontró el archivo local {local_path}")
            
    sftp.close()
    print("Transferencia de archivos completada.")
    
    # 3. Configurar entorno de Python
    print("Configurando entorno virtual de Python en Linux...")
    run_ssh_cmd(ssh, f"cd {remote_dir} && python3 -m venv venv")
    
    # Instalar requerimientos
    print("Instalando dependencias en Linux (esto puede tardar un minuto)...")
    run_ssh_cmd(ssh, f"cd {remote_dir} && venv/bin/pip install -r requirements.txt")
    
    # Instalar Playwright Chromium
    print("Instalando navegadores de Playwright en Linux...")
    run_ssh_cmd(ssh, f"cd {remote_dir} && venv/bin/playwright install chromium")
    
    # Hacer el script ejecutor ejecutable
    run_ssh_cmd(ssh, f"chmod +x {remote_dir}/run_local.sh")
    
    # 4. Configurar cron job (cada hora)
    print("Configurando tarea programada (cron) en Linux...")
    cron_cmd = f"0 * * * * {remote_dir}/run_local.sh"
    
    # Comprobar si ya existe en crontab para no duplicarlo
    out, err = run_ssh_cmd(ssh, "crontab -l")
    if cron_cmd not in out:
        # Añadir al crontab
        new_cron = (out + "\n" + cron_cmd).strip() + "\n"
        # Usamos echo para escribir el nuevo crontab
        # Escapamos saltos de línea para enviarlo de forma segura
        stdin, stdout, stderr = ssh.exec_command("crontab -")
        stdin.write(new_cron)
        stdin.flush()
        stdin.close()
        print("Tarea agregada al crontab del usuario.")
    else:
        print("La tarea ya estaba configurada en el crontab.")
        
    print("\n¡Configuración completada con éxito!")
    print("El scraper ahora correrá cada hora en la máquina Linux a través de Cron.")
    ssh.close()

if __name__ == "__main__":
    main()
