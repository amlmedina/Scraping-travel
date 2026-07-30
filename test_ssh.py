import paramiko

def test():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        print("Intentando conectar a aj@100.104.159.51 con contraseña 101978...")
        ssh.connect('100.104.159.51', username='aj', password='101978')
    except Exception as e:
        print("Fallo de conexión:", e)
        return
    print("Conectado con éxito!")
    stdin, stdout, stderr = ssh.exec_command("echo %USERNAME%")
    print("Usuario en la máquina remota:", stdout.read().decode().strip())
    ssh.close()

if __name__ == '__main__':
    test()
