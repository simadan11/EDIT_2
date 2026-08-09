def run_skill(args, player=None):
    action = args.get('action')
    if action == 'send_message':
        return f"Выполнение действия Discord: Отправка сообщения в канал {args.get('channel_id')} с текстом: '{args.get('message_text')}'"
    elif action == 'manage_channel':
        return "Выполнение действия Discord: Управление каналом."
    elif action == 'set_status':
        return "Выполнение действия Discord: Установка статуса."
    else:
        return "Неизвестное действие Discord."