import asyncio
import os
import sys
import re
import winreg
from telethon import TelegramClient, functions
from telethon.errors import FloodWaitError

# файл с сохранёнными api данными, чтобы каждый раз не вводить
CONFIG_FILE = 'tg_cleanup_config.txt'
# сессия Telethon (сюда ляжет авторизация, не удаляй её без причины)
SESSION_NAME = 'cleanup_session'

API_ID = None
API_HASH = None
PHONE = None
# задержка между запросами, чтобы не словить FloodWait раньше времени
DELAY_BETWEEN_ACTIONS = 3.0
client = None


# читаем сохранённый конфиг, если он есть
def load_config():
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            lines = [l.strip() for l in f.readlines()]
        if len(lines) >= 3:
            return {
                'api_id': int(lines[0]),
                'api_hash': lines[1],
                'phone': lines[2],
            }
    except Exception:
        pass
    return None


# сохраняем api-данные, чтобы юзер не парился в следующий раз
def save_config(api_id, api_hash, phone):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        f.write(f"{api_id}\n{api_hash}\n{phone}\n")
    print(f"[+] Настройки сохранены в {CONFIG_FILE}")


# достаём системный прокси из реестра винды — эта хуйня нужна только на win32
def get_windows_system_proxy():
    if sys.platform != 'win32':
        print("[!] Определение системного прокси поддерживается только на Windows.")
        return None
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            try:
                proxy_enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            except FileNotFoundError:
                proxy_enable = 0
            if not proxy_enable:
                print("[*] Системный прокси Windows выключен.")
                return None
            try:
                proxy_server, _ = winreg.QueryValueEx(key, "ProxyServer")
            except FileNotFoundError:
                print("[!] ProxyEnable=1, но ProxyServer не задан.")
                return None
            if not proxy_server:
                return None
            proxy_server = proxy_server.strip()
            # бывает формат "socks=http://1.2.3.4:1080;http=..." — разбираем
            if '=' in proxy_server:
                parts = dict(
                    p.split('=', 1) for p in proxy_server.split(';') if '=' in p
                )
                for proto in ('socks', 'socks5', 'https', 'http'):
                    if proto in parts:
                        proxy_server = parts[proto]
                        break
            match = re.match(r'^(.+?):(\d+)$', proxy_server)
            if not match:
                print(f"[!] Не удалось разобрать прокси: {proxy_server}")
                return None
            addr = match.group(1).strip()
            port = int(match.group(2))
            # грубо угадываем тип по порту, обычно так и есть
            proxy_type = 'socks5' if port in (1080, 1081, 9050) else 'http'
            print(f"[+] Найден системный прокси: {proxy_type}://{addr}:{port}")
            return (proxy_type, addr, port)
    except ImportError:
        print("[!] Модуль winreg недоступен.")
        return None
    except Exception as e:
        print(f"[!] Ошибка чтения системного прокси: {e}")
        return None


# спрашиваем api_id / api_hash / телефон с валидацией
def _ask_api_data():
    while True:
        raw = input("api_id (только цифры): ").strip()
        raw = ''.join(ch for ch in raw if ch.isdigit())
        if raw:
            api_id = int(raw)
            break
        print("[!] api_id должен состоять из цифр.")
    while True:
        api_hash = input("api_hash (32 символа): ").strip().replace(' ', '')
        if len(api_hash) >= 30:
            break
        print("[!] api_hash слишком короткий.")
    while True:
        phone = input("Номер телефона (+71234567890): ").strip()
        phone = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
        if phone.startswith('+') and phone[1:].isdigit() and len(phone) >= 8:
            break
        if phone.isdigit() and len(phone) >= 8:
            phone = '+' + phone
            break
        print("[!] Неверный формат. Пример: +71234567890")
    return api_id, api_hash, phone


# основной диалог: креды + прокси
def ask_credentials():
    print("=" * 55)
    print("       НАСТРОЙКА ПОДКЛЮЧЕНИЯ К TELEGRAM")
    print("=" * 55)
    print("Данные можно вставить через Ctrl+V (или ПКМ → Вставить).")
    print("Получить api_id и api_hash: https://my.telegram.org")
    print("=" * 55)

    saved = load_config()
    if saved:
        print(f"\n[+] Найдены сохранённые настройки:")
        print(f"    api_id  = {saved['api_id']}")
        print(f"    api_hash = {saved['api_hash'][:6]}...{saved['api_hash'][-4:]}")
        print(f"    phone   = {saved['phone']}")
        use_saved = input("Использовать их? (yes/no): ").strip().lower()
        if use_saved in ('y', 'yes', 'да', 'д'):
            api_id, api_hash, phone = saved['api_id'], saved['api_hash'], saved['phone']
        else:
            api_id, api_hash, phone = _ask_api_data()
    else:
        api_id, api_hash, phone = _ask_api_data()

    save_choice = input("Сохранить api-данные для следующих запусков? (yes/no): ").strip().lower()
    if save_choice in ('y', 'yes', 'да', 'д'):
        save_config(api_id, api_hash, phone)

    print("\n" + "-" * 55)
    print("ПРОКСИ")
    print("-" * 55)

    sys_proxy = get_windows_system_proxy()
    use_sys = False

    if sys_proxy:
        ans = input("Использовать системный прокси Windows? (yes/no): ").strip().lower()
        if ans in ('y', 'yes', 'да', 'д'):
            use_sys = True

    if not use_sys:
        print("\nВведите прокси вручную (Enter — без прокси).")
        print("Формат: socks5://user:pass@host:port  или  http://host:port")
        proxy_raw = input("Прокси (Enter = пропустить): ").strip()
        if proxy_raw:
            match = re.match(
                r'^(?P<type>socks5|socks4|http|https)://'
                r'(?:(?P<user>[^:@]+):(?P<pass>[^@]+)@)?'
                r'(?P<host>[^:]+):(?P<port>\d+)$',
                proxy_raw, re.IGNORECASE
            )
            if match:
                ptype = match.group('type').lower()
                if ptype == 'https':
                    ptype = 'http'
                sys_proxy = (ptype, match.group('host'), int(match.group('port')),
                             match.group('user'), match.group('pass'))
            else:
                print("[!] Не удалось разобрать прокси. Продолжаю без прокси.")

    return api_id, api_hash, phone, sys_proxy


# мелкая обёртка чтобы не писать asyncio.sleep каждый раз
async def safe_sleep(seconds=None):
    await asyncio.sleep(seconds if seconds is not None else DELAY_BETWEEN_ACTIONS)


# удаляем все свои сообщения во всех диалогах, пачками по 100
async def delete_all_messages():
    print("\n[*] Удаление всех сообщений пачками...")
    dialogs = await client.get_dialogs()
    total_deleted = 0
    total_dialogs = len(dialogs)

    for idx, dialog in enumerate(dialogs, 1):
        try:
            print(f"[{idx}/{total_dialogs}] Обрабатываю: {dialog.name}")
            batch = []
            count_in_dialog = 0

            # from_user='me' — только свои сообщения, чужие нам не нужны
            async for message in client.iter_messages(dialog.id, from_user='me'):
                batch.append(message.id)
                if len(batch) >= 100:
                    try:
                        await client(functions.messages.DeleteMessagesRequest(
                            id=batch, revoke=True
                        ))
                        total_deleted += len(batch)
                        count_in_dialog += len(batch)
                        print(f"    ... удалено {count_in_dialog} в этом чате")
                        batch = []
                        await safe_sleep()
                    except FloodWaitError as e:
                        print(f"[!] FloodWait: жду {e.seconds} сек...")
                        await asyncio.sleep(e.seconds + 5)
                    except Exception as e:
                        print(f"[!] Ошибка удаления пачки: {e}")
                        batch = []

            # добиваем остаток, который не влез в последнюю сотню
            if batch:
                try:
                    await client(functions.messages.DeleteMessagesRequest(
                        id=batch, revoke=True
                    ))
                    total_deleted += len(batch)
                    count_in_dialog += len(batch)
                except Exception as e:
                    print(f"[!] Ошибка удаления остатка: {e}")

            if count_in_dialog:
                print(f"    [+] В чате удалено: {count_in_dialog}")
        except FloodWaitError as e:
            print(f"[!] FloodWait на диалоге: жду {e.seconds} сек...")
            await asyncio.sleep(e.seconds + 5)
        except Exception as e:
            print(f"[!] Ошибка диалога {dialog.name}: {e}")

    print(f"[+] Всего удалено сообщений: {total_deleted}")


# выходим из всех групп и каналов (кроме своих собственных — их не тронем)
async def leave_all_chats_and_channels():
    print("\n[*] Выход из групп и каналов...")
    dialogs = await client.get_dialogs()
    left_count = 0
    for dialog in dialogs:
        try:
            if dialog.is_user:
                continue
            if dialog.is_channel:
                try:
                    full = await client(functions.channels.GetFullChannelRequest(dialog.entity))
                    # если ты владелец — сваливать нельзя, скипаем
                    if full.full_chat.creator:
                        print(f"[*] Пропускаю (вы владелец): {dialog.name}")
                        continue
                except Exception:
                    pass
            print(f"[*] Выхожу из: {dialog.name}")
            await client(functions.channels.LeaveChannelRequest(dialog.entity))
            left_count += 1
            await safe_sleep()
        except FloodWaitError as e:
            print(f"[!] FloodWait: жду {e.seconds} сек...")
            await asyncio.sleep(e.seconds + 5)
        except Exception as e:
            print(f"[!] Ошибка выхода из {dialog.name}: {e}")
    print(f"[+] Покинуто чатов/каналов: {left_count}")


# выходим ТОЛЬКО из каналов (broadcast), группы не трогаем
async def leave_all_channels_silent():
    print("\n[*] Выход только из каналов...")
    dialogs = await client.get_dialogs()
    left = 0
    for dialog in dialogs:
        try:
            if not dialog.is_channel:
                continue
            if getattr(dialog.entity, 'broadcast', False):
                try:
                    full = await client(functions.channels.GetFullChannelRequest(dialog.entity))
                    if full.full_chat.creator:
                        continue
                except Exception:
                    pass
                print(f"[*] Выхожу из канала: {dialog.name}")
                await client(functions.channels.LeaveChannelRequest(dialog.entity))
                left += 1
                await safe_sleep()
        except FloodWaitError as e:
            print(f"[!] FloodWait: жду {e.seconds} сек...")
            await asyncio.sleep(e.seconds + 5)
        except Exception as e:
            print(f"[!] Ошибка: {e}")
    print(f"[+] Покинуто каналов: {left}")


# выходим только из групп, каналы не трогаем
async def leave_all_groups_silent():
    print("\n[*] Выход только из групп...")
    dialogs = await client.get_dialogs()
    left = 0
    for dialog in dialogs:
        try:
            if not dialog.is_group:
                continue
            if dialog.is_channel:
                try:
                    full = await client(functions.channels.GetFullChannelRequest(dialog.entity))
                    if full.full_chat.creator:
                        continue
                except Exception:
                    pass
            print(f"[*] Выхожу из группы: {dialog.name}")
            # у обычных групп и супергрупп разные методы, вот и подстраховываемся
            try:
                await client(functions.channels.LeaveChannelRequest(dialog.entity))
            except Exception:
                await client.delete_dialog(dialog.entity)
            left += 1
            await safe_sleep()
        except FloodWaitError as e:
            print(f"[!] FloodWait: жду {e.seconds} сек...")
            await asyncio.sleep(e.seconds + 5)
        except Exception as e:
            print(f"[!] Ошибка: {e}")
    print(f"[+] Покинуто групп: {left}")


# блокируем и сразу сносим диалог с ботами
async def block_and_delete_bots():
    print("\n[*] Блокировка и удаление ботов...")
    dialogs = await client.get_dialogs()
    blocked_count = 0
    for dialog in dialogs:
        try:
            if not dialog.is_user:
                continue
            entity = await client.get_entity(dialog.id)
            if not getattr(entity, 'bot', False):
                continue
            print(f"[*] Блокирую бота: {dialog.name}")
            await client(functions.contacts.BlockRequest(id=entity))
            await client.delete_dialog(entity)
            blocked_count += 1
            await safe_sleep()
        except FloodWaitError as e:
            print(f"[!] FloodWait: жду {e.seconds} сек...")
            await asyncio.sleep(e.seconds + 5)
        except Exception as e:
            print(f"[!] Ошибка с ботом {dialog.name}: {e}")
    print(f"[+] Заблокировано ботов: {blocked_count}")


# сносим все контакты одним запросом — так быстрее
async def delete_all_contacts():
    print("\n[*] Удаление контактов...")
    result = await client(functions.contacts.GetContactsRequest(hash=0))
    contact_ids = [c.id for c in result.users]
    if contact_ids:
        try:
            await client(functions.contacts.DeleteContactsRequest(id=contact_ids))
            print(f"[+] Удалено контактов: {len(contact_ids)}")
        except FloodWaitError as e:
            print(f"[!] FloodWait: жду {e.seconds} сек...")
            await asyncio.sleep(e.seconds + 5)
    else:
        print("[*] Контактов нет.")


# удаляем все аватарки профиля
async def clear_profile_photos():
    print("\n[*] Удаление аватарок...")
    try:
        photos = await client(functions.photos.GetUserPhotosRequest(
            user_id='me', offset=0, max_id=0, limit=100
        ))
        if not photos.photos:
            print("[*] Аватарок нет.")
            return
        for photo in photos.photos:
            try:
                await client(functions.photos.DeletePhotosRequest(id=[photo]))
                print("[*] Удалена аватарка.")
                await safe_sleep()
            except FloodWaitError as e:
                print(f"[!] FloodWait: жду {e.seconds} сек...")
                await asyncio.sleep(e.seconds + 5)
            except Exception as e:
                print(f"[!] Ошибка удаления аватарки: {e}")
        print("[+] Аватарки удалены.")
    except Exception as e:
        print(f"[!] Ошибка получения аватарок: {e}")


# чистим bio — просто отправляем пустую строку
async def clear_profile_bio():
    print("\n[*] Очистка bio...")
    try:
        await client(functions.account.UpdateProfileRequest(about=""))
        print("[+] Bio очищено.")
    except FloodWaitError as e:
        print(f"[!] FloodWait: жду {e.seconds} сек...")
        await asyncio.sleep(e.seconds + 5)
    except Exception as e:
        print(f"[!] Ошибка очистки bio: {e}")


# снимаем username (пустая строка = удалить)
async def clear_username():
    print("\n[*] Удаление username...")
    try:
        await client(functions.account.UpdateUsernameRequest(username=""))
        print("[+] Username удалён.")
    except FloodWaitError as e:
        print(f"[!] FloodWait: жду {e.seconds} сек...")
        await asyncio.sleep(e.seconds + 5)
    except Exception as e:
        print(f"[!] Ошибка удаления username: {e}")


# сносим все личные чаты, опционально с блокировкой собеседников
async def delete_all_private_chats():
    print("\n[*] Удаление всех личных чатов (ЛС)...")
    dialogs = await client.get_dialogs()
    deleted = 0
    total = 0
    for dialog in dialogs:
        if not dialog.is_user:
            continue
        if getattr(dialog.entity, 'bot', False):
            continue
        total += 1
    print(f"[*] Найдено личных чатов: {total}")
    ask_block = input("Блокировать собеседников? (yes/no): ").strip().lower()
    do_block = ask_block in ('y', 'yes', 'да', 'д')
    for idx, dialog in enumerate(dialogs, 1):
        try:
            if not dialog.is_user:
                continue
            if getattr(dialog.entity, 'bot', False):
                continue
            print(f"[{idx}] Удаляю ЛС: {dialog.name}")
            try:
                await client.delete_dialog(dialog.entity)
                deleted += 1
            except FloodWaitError as e:
                print(f"[!] FloodWait: жду {e.seconds} сек...")
                await asyncio.sleep(e.seconds + 5)
            except Exception as e:
                print(f"[!] Ошибка удаления диалога {dialog.name}: {e}")
            if do_block:
                try:
                    await client(functions.contacts.BlockRequest(id=dialog.entity))
                except FloodWaitError as e:
                    print(f"[!] FloodWait: жду {e.seconds} сек...")
                    await asyncio.sleep(e.seconds + 5)
                except Exception as e:
                    print(f"[!] Ошибка блокировки {dialog.name}: {e}")
            await safe_sleep()
        except FloodWaitError as e:
            print(f"[!] FloodWait: жду {e.seconds} сек...")
            await asyncio.sleep(e.seconds + 5)
        except Exception as e:
            print(f"[!] Ошибка: {e}")
    print(f"[+] Удалено личных чатов: {deleted}")


# карта действий: цифра -> (описание, функция)
ACTIONS = {
    '1':  ("Удалить все сообщения во всех диалогах", delete_all_messages),
    '2':  ("Выйти из всех групп и каналов",          leave_all_chats_and_channels),
    '3':  ("Выйти только из каналов",                leave_all_channels_silent),
    '4':  ("Выйти только из групп",                  leave_all_groups_silent),
    '5':  ("Заблокировать и удалить всех ботов",     block_and_delete_bots),
    '6':  ("Удалить все контакты",                   delete_all_contacts),
    '7':  ("Удалить все аватарки профиля",           clear_profile_photos),
    '8':  ("Очистить bio профиля",                   clear_profile_bio),
    '9':  ("Удалить username",                       clear_username),
    '10': ("Удалить все личные чаты (ЛС)",           delete_all_private_chats),
}


def print_menu():
    print("\n" + "=" * 55)
    print("          МЕНЮ ОЧИСТКИ АККАУНТА")
    print("=" * 55)
    for key, (desc, _) in ACTIONS.items():
        print(f"  {key}. {desc}")
    print("  a. Выполнить ВСЁ (по очереди, с паузами)")
    print("  0. Выход")
    print("=" * 55)
    print("Введите номера через запятую (например: 1,3,5)")
    print("или 'a' для всех действий, '0' для выхода.\n")


# запуск выбранных пользователем действий
async def run_selected(choice: str):
    choice = choice.strip().lower()
    if choice == '0':
        return False
    if choice == 'a':
        print("\n[!] Выбрано ВЫПОЛНИТЬ ВСЁ. Это займёт много времени.")
        if input("Продолжить? (yes/no): ").strip().lower() != 'yes':
            return True
        # порядок специально такой: сначала лёгкое, потом тяжёлое
        order = ['7', '8', '9', '6', '5', '10', '3', '4', '1', '2']
        for key in order:
            _, func = ACTIONS[key]
            await func()
            print("[*] Пауза 30 сек перед следующим действием...")
            await asyncio.sleep(30)
        return True
    keys = [k.strip() for k in choice.split(',') if k.strip() in ACTIONS]
    if not keys:
        print("[!] Неверный ввод.")
        return True
    print(f"\n[*] Выбрано действий: {len(keys)}")
    for key in keys:
        desc, _ = ACTIONS[key]
        print(f"  → {desc}")
    if input("Запустить? (yes/no): ").strip().lower() != 'yes':
        return True
    for i, key in enumerate(keys):
        desc, func = ACTIONS[key]
        print(f"\n{'='*55}\n>>> {desc}\n{'='*55}")
        await func()
        if i != len(keys) - 1:
            print("[*] Пауза 20 сек перед следующим действием...")
            await asyncio.sleep(20)
    return True


async def main():
    global API_ID, API_HASH, PHONE, DELAY_BETWEEN_ACTIONS, client

    API_ID, API_HASH, PHONE, proxy_cfg = ask_credentials()

    delay_raw = input(
        f"\nЗадержка между действиями в секундах (Enter = {DELAY_BETWEEN_ACTIONS}): "
    ).strip()
    if delay_raw:
        try:
            # меньше 2 сек ставить нет смысла — словишь флуд
            DELAY_BETWEEN_ACTIONS = max(2.0, float(delay_raw.replace(',', '.')))
        except ValueError:
            print(f"[!] Неверное значение, использую {DELAY_BETWEEN_ACTIONS} сек.")
    print(f"[+] Задержка: {DELAY_BETWEEN_ACTIONS} сек.")

    print("\n[*] Подключаюсь к Telegram...")

    client_kwargs = {}
    if proxy_cfg:
        # тут может быть либо кортеж из 5 (с логином/паролем), либо 3
        if len(proxy_cfg) == 5:
            ptype, addr, port, user, pwd = proxy_cfg
            proxy_dict = {
                'proxy_type': ptype,
                'addr': addr,
                'port': port,
                'rdns': True,
            }
            if user:
                proxy_dict['username'] = user
                proxy_dict['password'] = pwd
            client_kwargs['proxy'] = proxy_dict
        else:
            ptype, addr, port = proxy_cfg
            client_kwargs['proxy'] = {
                'proxy_type': ptype,
                'addr': addr,
                'port': port,
                'rdns': True,
            }
        print(f"[+] Прокси активен: {ptype}://{addr}:{port}")

    client = TelegramClient(SESSION_NAME, API_ID, API_HASH, **client_kwargs)

    try:
        await client.start(phone=PHONE)
    except Exception as e:
        print(f"[!] Ошибка подключения: {e}")
        print("[!] Если используете прокси — проверьте, что он работает и тип верный.")
        return

    print("[+] Аккаунт подключён.")

    # бесконечное меню, пока юзер сам не выберет 0
    while True:
        print_menu()
        choice = input("Ваш выбор: ").strip()
        try:
            cont = await run_selected(choice)
        except KeyboardInterrupt:
            print("\n[!] Прервано пользователем.")
            break
        except Exception as e:
            print(f"[!] Ошибка: {e}")
            cont = True
        if not cont:
            break
        if input("\nВернуться в меню? (yes/no): ").strip().lower() not in ('y', 'yes', 'да', 'д'):
            break

    print("[+] Отключаюсь.")
    await client.disconnect()


if __name__ == '__main__':
    asyncio.run(main())