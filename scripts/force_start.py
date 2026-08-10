import socket
import sys
import os
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "project", "backend")

def try_bind(port):
    """Try to create a socket with SO_REUSEADDR and start uvicorn"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('0.0.0.0', port))
        sock.listen(1)
        # Pass the socket fd to uvicorn
        fd = sock.fileno()
        print(f'Successfully bound to port {port} with fd {fd}')
        sock.close()
        return True
    except OSError as e:
        print(f'Cannot bind to port {port}: {e}')
        return False

port = 2001
if try_bind(port):
    print('Port is available, starting uvicorn...')
    os.environ['PYTHONPATH'] = PROJECT_ROOT
    os.chdir(BACKEND_DIR)
    subprocess.run([sys.executable, '-m', 'uvicorn', 'app.main:app',
                   '--host', '127.0.0.1', '--port', str(port), '--no-proxy-headers'])
else:
    print('Port still occupied. Trying to start on port 2001 anyway...')
    # The zombie socket might allow a new bind with SO_REUSEADDR
    os.environ['PYTHONPATH'] = PROJECT_ROOT
    os.chdir(BACKEND_DIR)
    subprocess.run([sys.executable, '-m', 'uvicorn', 'app.main:app',
                   '--host', '127.0.0.1', '--port', str(port), '--no-proxy-headers'])
