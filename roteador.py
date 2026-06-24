# Desenvolvedores
# Bernardo Vale dos Santos Bento - 2023002065
# Pedro Henrique Egito Aguiar - 2023002066

import socket
import sys
import threading
import select
from struct import *

####################################################################
# Constantes
####################################################################
INFINITO = 16
INTERVALO_DV = 1  # segundos entre anúncios de vetor de distâncias

####################################################################
# Estruturas de dados
####################################################################
my_name = ""
vizinhos = {}           # {nome: socket}
socket_to_name = {}     # {socket: nome}

# {destino: (proximo_passo, distancia)}
tabela_roteamento = {}

####################################################################
# Essas funções fazem a separação dos campos da mensagem recebida.
####################################################################
def extrai_roteador(msg):
    r = unpack("!32s", msg)
    return r[0].decode().rstrip('\x00')

def extrai_endereco(msg):
    r = unpack("!32sH", msg)
    return r[0].decode().rstrip('\x00'), r[1]

def extrai_destino_texto(msg):
    l = unpack("!32s64s", msg)
    destino = l[0].decode().rstrip('\x00')
    texto   = l[1].decode().rstrip('\x00')
    return destino, texto

dv_ativo = False  # flag: envio periódico iniciado

####################################################################
# Timer periódico
####################################################################

def recv_exato(sock, n):
    """Garante receber exatamente n bytes do socket."""
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Socket fechado durante recv")
        buf += chunk
    return buf

def disparar_dv():
    """Envia DV para vizinhos e reagenda timer."""
    if vizinhos:
        enviar_vetor_todos()
    t = threading.Timer(INTERVALO_DV, disparar_dv)
    t.daemon = True  # timer não impede encerramento do processo
    t.start()


def montar_vetor(destino_para_vizinho=None):
    """
    Monta mensagem V com tabela local.
    destino_para_vizinho: nome do vizinho destinatário (para poison reverse).
    """
    entradas = []
    for destino, (proximo, dist) in tabela_roteamento.items():
        # Poison reverse: se rota para 'destino' passa por este vizinho, anuncia inf
        d = INFINITO if (destino_para_vizinho and proximo == destino_para_vizinho) else dist
        entradas.append((destino, d))

    n = len(entradas)
    msg = pack("!c32sH", b'V', my_name.encode(), n)
    for destino, dist in entradas:
        msg += pack("!32sH", destino.encode(), dist)
    return msg

def desmontar_vetor(sock):
    """
    Lê mensagem V do socket (byte 'V' já consumido).
    Retorna (nome_remetente, [(destino, dist), ...]).
    """
    # Cabeçalho fixo: 32s nome + H num_entradas
    header = recv_exato(sock, 34)
    nome_remetente = unpack("!32s", header[:32])[0].decode().rstrip('\x00')
    n = unpack("!H", header[32:34])[0]

    entradas = []
    for _ in range(n):
        entrada = recv_exato(sock, 34)  # 32s destino + H dist
        destino = unpack("!32s", entrada[:32])[0].decode().rstrip('\x00')
        dist    = unpack("!H", entrada[32:34])[0]
        entradas.append((destino, dist))

    return nome_remetente, entradas

def enviar_vetor_todos():
    """Envia vetor de distâncias para todos os vizinhos (com poison reverse)."""
    for nome_viz, sock_viz in list(vizinhos.items()):
        try:
            msg = montar_vetor(destino_para_vizinho=nome_viz)
            sock_viz.send(msg)
        except Exception:
            # Sokcet fechado em outra trhead
            # o send() falhará. Ignoramos o erro aqui pois o 'select' da thread
            # principal fará a limpeza logo em seguida.
            pass

def atualizar_tabela(nome_remetente, entradas):
    """Bellman-Ford: atualiza tabela_roteamento com vetor recebido de nome_remetente."""
    alterou = False
    for destino, dist_anunciada in entradas:
        if destino == my_name:
            continue
        nova_dist = min(dist_anunciada + 1, INFINITO)
        if destino not in tabela_roteamento:
            tabela_roteamento[destino] = (nome_remetente, nova_dist)
            alterou = True
        else:
            proximo_atual, dist_atual = tabela_roteamento[destino]
            if proximo_atual == nome_remetente:
                if dist_atual != nova_dist:
                    tabela_roteamento[destino] = (nome_remetente, nova_dist)
                    alterou = True
            elif nova_dist < dist_atual:
                tabela_roteamento[destino] = (nome_remetente, nova_dist)
                alterou = True
    return alterou

####################################################################
# Funções de conexão com vizinhos
####################################################################
def conectar_vizinho(host, porto):
    """Lado ativo do handshake: conecta, envia nome, recebe nome do vizinho."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host, porto))
    # Envia próprio nome primeiro
    s.send(pack("!32s", my_name.encode()))
    # Recebe nome do outro lado
    nome_msg = recv_exato(s, 32)
    nome_vizinho = unpack("!32s", nome_msg)[0].decode().rstrip('\x00')
    vizinhos[nome_vizinho] = s
    socket_to_name[s] = nome_vizinho
    return nome_vizinho

####################################################################
# Início do programa: aguarda a conexão do programa de controle
####################################################################
server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
# SO_REUSEADDR evita o erro temporário "address already in use" que 
# pode aparecer em alguns casos quando um servidor termina de forma anormal

# depois de criar o socket, faz o bind, listen e acept da primeira conexão
server_port = int(sys.argv[1])
server_socket.bind(('', server_port))
server_socket.listen(5)
control, ctrl_addr = server_socket.accept()

# Recebe nome do roteador
my_name_msg = recv_exato(control, 32)
l = unpack("!32s", my_name_msg)
my_name = l[0].decode().rstrip('\x00')

# Inicializa tabela com rota para si mesmo
tabela_roteamento[my_name] = (my_name, 0)

####################################################################
# a partir deste ponto, certamente seu programa precisará ser alterado
# para incluir o uso do select para observar as conexões existentes e
# novas que surjam de outros roteadores, bem como enviar periodicamente
# as mensagens do protocolo de roteamento para os seus vizinhos imediados
####################################################################

while(True): # aguarda mensagens do comando de controle
    
    socks_read = [server_socket, control] + list(vizinhos.values())
    readables, _, _ = select.select(socks_read, [], [])

    for sock in readables:

        # Nova conexão incoming (vizinho conectando neste roteador)
        if sock == server_socket:
            novo_sock, addr = server_socket.accept()
            # Lado passivo: lê nome do vizinho que conectou
            nome_msg = recv_exato(novo_sock, 32)
            nome_vizinho = unpack("!32s", nome_msg)[0].decode().rstrip('\x00')
            # Responde com próprio nome
            novo_sock.send(pack("!32s", my_name.encode()))
            vizinhos[nome_vizinho] = novo_sock
            socket_to_name[novo_sock] = nome_vizinho

        # Mensagem do controle
        elif sock == control: 
            msg = control.recv(1) # no roteador, não haverá apenas essa conexão
            if not msg:
                print("Connection closed", flush=True)
                sys.exit()
            c = unpack("!c", msg)
            comando = c[0].decode()

            if comando == 'C':
                # o roteador recebe o ENDEREÇO do outro roteador ao qual se conectar
                msg = recv_exato(control, 34) # 32s host + H porto
                host, porto = extrai_endereco(msg)
                nome_vizinho = conectar_vizinho(host, porto)
                # Adiciona vizinho na tabela com dist=1
                tabela_roteamento[nome_vizinho] = (nome_vizinho, 1)

            elif comando == 'D':
                # o roteador recebe o NOME do outro roteador que deve ser removido
                # da sua lista de conexões
                msg = recv_exato(control, 32)
                nome_vizinho = extrai_roteador(msg)
                sock_viz = vizinhos.pop(nome_vizinho, None)
                if sock_viz:
                    socket_to_name.pop(sock_viz, None)
                    try:
                        sock_viz.close()
                    except Exception:
                        pass
                for destino in tabela_roteamento:
                    proximo, dist = tabela_roteamento[destino]
                    if proximo == nome_vizinho:
                        tabela_roteamento[destino] = (nome_vizinho, INFINITO)
                enviar_vetor_todos()

            elif comando == 'E':
                # o roteador recebe o NOME do outro destino e o texto
                msg = recv_exato(control, 96)
                destino, texto = extrai_destino_texto(msg)
                # 2.4: rotear mensagem 
                if destino == my_name:
                    print(f"R {texto}", flush=True)
                else:
                    if destino in tabela_roteamento:
                        proximo, dist = tabela_roteamento[destino]
                        if dist < INFINITO and proximo in vizinhos:
                            print(f"E {destino} {proximo} {texto}", flush=True)
                            msg_env = b'E' + pack("!32s64s",
                                                  destino.encode(),
                                                  texto.encode())
                            vizinhos[proximo].send(msg_env)
            
            elif comando == 'T':
                for destino, (proximo, dist) in tabela_roteamento.items():
                    print(f"T {destino} {proximo} {dist}", flush=True)

            elif comando == 'I':
                if not dv_ativo:
                    dv_ativo = True
                    disparar_dv()

            else:
                pass

        # Mensagem de vizinho
        else:
            msg = sock.recv(1)
            if not msg:
                # Vizinho fechou conexão
                nome_caiu = socket_to_name.get(sock, None)
                if nome_caiu:
                    vizinhos.pop(nome_caiu, None)
                    socket_to_name.pop(sock, None)
                    try:
                        sock.close()
                    except Exception:
                        pass
                    for destino in tabela_roteamento:
                        proximo, dist = tabela_roteamento[destino]
                        if proximo == nome_caiu:
                            tabela_roteamento[destino] = (nome_caiu, INFINITO)
                    enviar_vetor_todos()
                continue
            else:
                tipo = unpack("!c", msg)[0].decode()
                if tipo == 'V':
                    # Recebe vetor de distâncias 
                    nome_remetente, entradas = desmontar_vetor(sock)
                    alterou = atualizar_tabela(nome_remetente, entradas)
                    if alterou:
                        enviar_vetor_todos()
                elif tipo == 'E':
                    # Mensagem roteada chegou
                    dados = recv_exato(sock, 96)
                    destino, texto = extrai_destino_texto(dados)
                    if destino == my_name:
                        print(f"R {texto}", flush=True)
                    else:
                        if destino in tabela_roteamento:
                            proximo, dist = tabela_roteamento[destino]
                            if dist < INFINITO and proximo in vizinhos:
                                print(f"E {destino} {proximo} {texto}", flush=True)
                                msg_env = b'E' + pack("!32s64s",
                                                      destino.encode(),
                                                      texto.encode())
                                vizinhos[proximo].send(msg_env)