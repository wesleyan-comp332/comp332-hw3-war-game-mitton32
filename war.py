"""
war card game client and server
"""
import asyncio
from collections import namedtuple
from enum import Enum
import logging
import random
import socket
import socketserver
import threading
import sys
from asyncio import IncompleteReadError
import uuid

Game = namedtuple("Game", ["p1", "p2"])

waiting_clients = []

class Command(Enum):
    WANTGAME = 0
    GAMESTART = 1
    PLAYCARD = 2
    PLAYRESULT = 3

class Result(Enum):
    WIN = 0
    DRAW = 1
    LOSE = 2

def readexactly(sock, numbytes):
    data = b''
    while len(data) < numbytes:
        more_data = sock.recv(numbytes - len(data))
        if not more_data:
            raise EOFError("Connection closed before all bytes could be read.")
        data += more_data
    return data

def kill_game(game):
    logging.info(f"Killing game between {game.p1.getpeername()} and {game.p2.getpeername()}")
    try:
        game.p1.close()
        game.p2.close()
    except Exception as e:
        logging.debug(f"Error closing sockets: {e}")
    for client in [game.p1, game.p2]:
        if client in waiting_clients:
            waiting_clients.remove(client)
    logging.info("Game killed and clients disconnected.")

def compare_cards(card1, card2):
    if card1 < card2:
        return -1
    elif card1 > card2:
        return 1
    return 0

def deal_cards():
    deck = list(range(52))
    random.shuffle(deck)
    return deck[:26], deck[26:]

def serve_game(host, port):
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((host, port))
    server_socket.listen(1000)
    logging.info(f"Server listening on {host}:{port}")

    def handle_game(client1, client2):
        try:
            msg1 = readexactly(client1, 2)
            msg2 = readexactly(client2, 2)
            if msg1[0] != Command.WANTGAME.value or msg1[1] != 0:
                kill_game(Game(client1, client2))
                return
            if msg2[0] != Command.WANTGAME.value or msg2[1] != 0:
                kill_game(Game(client1, client2))
                return

            hand1, hand2 = deal_cards()
            client1.send(bytes([Command.GAMESTART.value]) + bytes(hand1))
            client2.send(bytes([Command.GAMESTART.value]) + bytes(hand2))

            used_cards1 = set()
            used_cards2 = set()

            for round_num in range(26):
                card1 = readexactly(client1, 2)
                card2 = readexactly(client2, 2)

                if card1[0] != Command.PLAYCARD.value or card2[0] != Command.PLAYCARD.value:
                    kill_game(Game(client1, client2))
                    return

                if card1[1] in used_cards1 or card2[1] in used_cards2:
                    kill_game(Game(client1, client2))
                    return

                used_cards1.add(card1[1])
                used_cards2.add(card2[1])

                result = compare_cards(card1[1], card2[1])
                if result == -1:
                    client1.send(bytes([Command.PLAYRESULT.value, Result.LOSE.value]))
                    client2.send(bytes([Command.PLAYRESULT.value, Result.WIN.value]))
                elif result == 1:
                    client1.send(bytes([Command.PLAYRESULT.value, Result.WIN.value]))
                    client2.send(bytes([Command.PLAYRESULT.value, Result.LOSE.value]))
                else:
                    client1.send(bytes([Command.PLAYRESULT.value, Result.DRAW.value]))
                    client2.send(bytes([Command.PLAYRESULT.value, Result.DRAW.value]))

            logging.info("Game over, disconnecting clients.")
        except Exception as e:
            logging.error(f"Game error: {e}")
            kill_game(Game(client1, client2))
        finally:
            try:
                client1.close()
                client2.close()
            except:
                pass

    while True:
        logging.info("Waiting for new players...")
        client1, addr1 = server_socket.accept()
        logging.info(f"Client 1 connected from {addr1} {client1}")
        client2, addr2 = server_socket.accept()
        logging.info(f"Client 2 connected from {addr2} {client2}")
        threading.Thread(target=handle_game, args=(client1, client2), daemon=True).start()

async def limit_client(host, port, loop, sem, delay):
    async with sem:
        await asyncio.sleep(delay)  # Spread out connections
        return await client(host, port, loop)


async def client(host, port, loop):
    try:
        reader, writer = await asyncio.open_connection(host, port)
        writer.write(b"\0\0")
        card_msg = await reader.readexactly(27)
        myscore = 0
        for card in card_msg[1:]:
            writer.write(bytes([Command.PLAYCARD.value, card]))
            result = await reader.readexactly(2)
            if result[1] == Result.WIN.value:
                myscore += 1
            elif result[1] == Result.LOSE.value:
                myscore -= 1
        if myscore > 0:
            result = "won"
        elif myscore < 0:
            result = "lost"
        else:
            result = "drew"
        logging.debug("Game complete, I %s", result)
        writer.close()
        await writer.wait_closed()
        return 1
    except ConnectionResetError:
        logging.error("ConnectionResetError")
        return 0
    except IncompleteReadError as e:
        logging.error(f"IncompleteReadError: {e}")
        return 0
    except OSError as e:
        logging.error(f"OSError: {e}")
        return 0

async def client_batch(host, port, num_clients):
    async def delayed_client(i):
        await asyncio.sleep(i * 0.01)
        return await client(host, port, asyncio.get_event_loop())

    tasks = [delayed_client(i) for i in range(num_clients)]
    completed = 0
    for task in asyncio.as_completed(tasks):
        result = await task
        completed += result
    logging.info(f"{completed}/{num_clients} clients completed.")


def main(args):
    """
    launch a client/server
    """
    host = args[1]
    port = int(args[2])
    if args[0] == "server":
        try:
            # your server should serve clients until the user presses ctrl+c
            serve_game(host, port)
        except KeyboardInterrupt:
            pass
        return
    else:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        
        asyncio.set_event_loop(loop)
        
    if args[0] == "client":
        loop.run_until_complete(client(host, port, loop))
    elif args[0] == "clients":
        sem = asyncio.Semaphore(1000)
        num_clients = int(args[3])
        clients = [
    limit_client(host, port, loop, sem, delay=0.01 * i)
    for i in range(num_clients)]
        async def run_all_clients():
            """
            use as_completed to spawn all clients simultaneously
            and collect their results in arbitrary order.
            """
            completed_clients = 0
            for client_result in asyncio.as_completed(clients):
                completed_clients += await client_result
            return completed_clients
        res = loop.run_until_complete(
            asyncio.Task(run_all_clients(), loop=loop))
        logging.info("%d completed clients", res)

    loop.close()

if __name__ == "__main__":
    # Changing logging to DEBUG
    logging.basicConfig(level=logging.DEBUG)
    main(sys.argv[1:])

