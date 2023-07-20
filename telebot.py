# Телеграмм бот. Управляет электромеханическим замком, получает статус бризера.

import datetime
import time
import telebot
from telebot import types
from config import token, broker, port, topic_pub, topic_sub, client_id, username, password, webhook_port, \
    webhook_ssl_cert_path, webhook_pk_path, web_hook_server, allowed_teleusers, admin_teleuser_id, psql_user, \
    psql_password, psql_host, psql_port, psql_database, telebot_id, command_open
from paho.mqtt import client as mqtt_client
import cherrypy
import psycopg2
from psycopg2 import Error

door_open_command = 'Открыть дверь'
door_open_success = 'Щёлк 🤞'
door_open_no_response = 'Что-то замок не отвечает. Если так и не открылся - попробуй еще раз или звони 🛎'
domofon_code_command = 'Код от подъезда'
domofon_code_key = ###
unknown_user = 'Ты кто?'
failed_to_read_from_db = 'Не получилось посмотреть статус бризера 😔'
degree_sign = u'\N{DEGREE SIGN}'

brizer_state = []

# Скорости бризера для сообщения в телеграмме
brizer_speed_dict = {
    1: '1⃣',
    2: '2⃣',
    3: '3⃣',
    4: '4⃣',
    5: '5⃣',
    6: '6⃣'
    }

# bot = telebot.TeleBot(token, parse_mode=None)

WEBHOOK_HOST = web_hook_server
WEBHOOK_PORT = webhook_port
WEBHOOK_LISTEN = web_hook_server

WEBHOOK_SSL_CERT = webhook_ssl_cert_path
WEBHOOK_SSL_PRIV = webhook_pk_path

WEBHOOK_URL_BASE = "https://%s:%s" % (WEBHOOK_HOST, WEBHOOK_PORT)
WEBHOOK_URL_PATH = "/%s/" % token

bot = telebot.TeleBot(token, parse_mode=None)


# Webhook server for telegram bot
class WebhookServer(object):
    @cherrypy.expose
    def index(self):
        if 'content-length' in cherrypy.request.headers and \
                'content-type' in cherrypy.request.headers and \
                cherrypy.request.headers['content-type'] == 'application/json':
            length = int(cherrypy.request.headers['content-length'])
            json_string = cherrypy.request.body.read(length).decode("utf-8")
            update = telebot.types.Update.de_json(json_string)
            # This function checks incoming messages from telebot
            bot.process_new_updates([update])
            return ''
        else:
            raise cherrypy.HTTPError(403)


def connect_mqtt():
    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            print("Connected to MQTT Broker!")
        else:
            print("Failed to connect, return code %d\n", rc)

    client = mqtt_client.Client(client_id)
    client.username_pw_set(username, password)
    client.on_connect = on_connect
    client.connect(broker, port)
    return client


def publish(client):
    msg = command_open
    result = client.publish(topic_pub, msg)
    status = result[0]
    if status == 0:
        print(f"Send `{msg}` to topic `{topic_pub}`")
    else:
        print(f"Failed to send message to topic {topic_pub}")


def send_to_mqtt():
    client = connect_mqtt()
    publish(client)
    client.disconnect()


msg1 = [0]  # To save response from ESP


def subscribe(client: mqtt_client):
    def on_message(client, userdata, msg):
        print(f"Received `{msg.payload.decode()}` from `{msg.topic}` topic")  # First line in log
        if msg.payload == b'Received':
            msg1[0] = msg.payload
            print('1 Message subscription: ', msg1[0])  # Second line in log

    client.on_message = on_message
    # return msg1


def receive_from_mqtt():
    client = connect_mqtt()
    subscribe(client)
    client.loop_start()
    print('Subscribing')

    x = 15
    while x != 0:
        client.subscribe(topic_sub)
        if msg1[0] in (b'esp last will: disconnected', 0, b'esp is connected to broker'):
            print(f"2 Received `{msg1}` from `{topic_sub}` topic")  # Third message in log
            time.sleep(1.0)
            x -= 1
        else:
            print(f"3 Received `{msg1}` from `{topic_sub}` topic")  # Alternative third line in log
            x = 0
    print('Subscribed to ' + topic_sub)
    # client.disconnect()
    print('Msg1 after time sleep: ', msg1[0])
    print('Stopping loop...')
    client.loop_stop()
    print('Finished subscription')


# Function to write into psql database
def write_to_db(
        telegram_bot_id,
        telegram_user_id,
        telegram_user_first_name,
        message_from_telegram_user,
        command_for_mqtt,
        time_sent_to_topic,
        sent_to_mqtt_topic,
        time_received_from_topic,
        received_from_mqtt_topic,
        message_received_from_mqtt_topic,
        command_execution_duration):
    try:
        # Connecting to database
        connection = psycopg2.connect(user=psql_user,
                                      password=psql_password,
                                      host=psql_host,
                                      port=psql_port,
                                      database=psql_database)
        cursor = connection.cursor()
        insert_query = """ INSERT INTO teledata (
            TELEGRAM_BOT_ID,
            TELEGRAM_USER_ID,
            TELEGRAM_USER_FIRST_NAME,
            MESSAGE_FROM_TELEGRAM_USER,
            COMMAND_FOR_MQTT,
            TIME_SENT_TO_TOPIC,
            SENT_TO_MQTT_TOPIC,
            TIME_RECEIVED_FROM_TOPIC,
            RECEIVED_FROM_MQTT_TOPIC,
            MESSAGE_RECEIVED_FROM_MQTT_TOPIC,
            COMMAND_EXECUTION_DURATION) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
        item_tuple = (
            telegram_bot_id,
            telegram_user_id,
            telegram_user_first_name,
            message_from_telegram_user,
            command_for_mqtt,
            time_sent_to_topic,
            sent_to_mqtt_topic,
            time_received_from_topic,
            received_from_mqtt_topic,
            message_received_from_mqtt_topic,
            command_execution_duration)
        cursor.execute(insert_query, item_tuple)
        connection.commit()
        print("Succesfully commited to DB")
    except (Exception, Error) as error:
        print("Failed to commit to DB with error:", error)
    finally:
        if connection:
            cursor.close()
            connection.close()
            print('Connection with PSQL closed successfully')
        else:
            print('Failed to close connection with DB')


def read_last_line_from_db():
    global brizer_state
    try:
        connection = psycopg2.connect(user=psql_user,
                                      password=psql_password,
                                      host=psql_host,
                                      port=psql_port,
                                      database=psql_database)
        print("Connected to database")
        cursor = connection.cursor()
        select_query = "select * from brizer_automation order by timestamp desc limit 1"
        cursor.execute(select_query)
        print("Fetching single row")
        brizer_state = cursor.fetchone()
        print(brizer_state)
        print('type: ', type(brizer_state))
        cursor.close()
    except (Exception, Error) as error:
        print("Failed to read from 'brizer_automation' table with error:", error)
    finally:
        if connection:
            connection.close()
            print('Connection with PSQL closed successfully')
        else:
            print('Failed to close connection with DB')
        return brizer_state


@bot.message_handler(commands=['open'])
def send_open_message(message):
    telegram_user_id = message.from_user.id
    telegram_user_first_name = message.from_user.first_name
    message_from_telegram_user = message.text
    command_for_mqtt = None
    time_sent_to_topic = None
    sent_to_mqtt_topic = None
    time_received_from_topic = None
    received_from_mqtt_topic = None
    message_received_from_mqtt_topic = None
    command_execution_duration = None
    if message.from_user.id in allowed_teleusers:
        markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=False)
        itembtn1 = types.KeyboardButton(door_open_command)
        itembtn2 = types.KeyboardButton(domofon_code_command)
        markup.add(itembtn1, itembtn2)
        send_to_mqtt()  # Sending command to ESP topic
        command_for_mqtt = command_open
        time_sent_to_topic = datetime.datetime.now().replace(microsecond=0)
        sent_to_mqtt_topic = topic_pub
        receive_from_mqtt()  # Waiting for an answer from ESP that command was executed
        if msg1 == [b'Received']:
            time_received_from_topic = datetime.datetime.now().replace(microsecond=0)
            command_execution_duration = (time_received_from_topic - time_sent_to_topic).seconds
            received_from_mqtt_topic = topic_sub
            message_received_from_mqtt_topic = str(msg1[0])
            print('ok')
            msg1[0] = 0  # Set msg1 to default to avoid error in this IF
            bot.send_chat_action(message.chat.id, 'typing')
            time.sleep(1)
            bot.send_message(message.chat.id, door_open_success, reply_markup=markup)
        else:
            bot.send_chat_action(message.chat.id, 'typing')
            time.sleep(1)
            bot.send_message(message.chat.id, door_open_no_response, reply_markup=markup)
    else:
        bot.send_chat_action(message.chat.id, 'typing')
        time.sleep(1)
        bot.send_message(message.chat.id, unknown_user)
    # Write send and received (if any) messages info to database
    write_to_db(telebot_id,
                telegram_user_id,
                telegram_user_first_name,
                message_from_telegram_user,
                command_for_mqtt,
                time_sent_to_topic,
                sent_to_mqtt_topic,
                time_received_from_topic,
                received_from_mqtt_topic,
                message_received_from_mqtt_topic,
                command_execution_duration)


@bot.message_handler(commands=['start', 'help', 'welcome'])
def send_welcome(message):
    telegram_user_id = message.from_user.id
    telegram_user_first_name = message.from_user.first_name
    message_from_telegram_user = message.text
    time_sent_to_topic = datetime.datetime.now().replace(microsecond=0)
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=False)
    itembtn1 = types.KeyboardButton(door_open_command)
    itembtn2 = types.KeyboardButton(domofon_code_command)
    markup.add(itembtn1, itembtn2)
    bot.send_chat_action(message.chat.id, 'typing')
    time.sleep(1)
    bot.send_message(message.chat.id, 'Привет, ' + message.from_user.first_name + ' 👋', reply_markup=markup)
    time.sleep(2)
    bot.send_chat_action(message.chat.id, 'typing')
    time.sleep(1)
    bot.send_message(message.chat.id, 'Этот бот знает код от домофона на улице', reply_markup=markup)
    time.sleep(2)
    bot.send_chat_action(message.chat.id, 'typing')
    time.sleep(1)
    bot.send_message(message.chat.id, 'И умеет открывать дверь около лифта', reply_markup=markup)
    time.sleep(2)
    bot.send_chat_action(message.chat.id, 'typing')
    time.sleep(1)
    bot.send_message(message.chat.id, 'Жми нужную кнопку внизу 👇', reply_markup=markup)
    write_to_db(telebot_id,
                telegram_user_id,
                telegram_user_first_name,
                message_from_telegram_user,
                None,
                time_sent_to_topic,
                None,
                None,
                None,
                None,
                None)


@bot.message_handler(commands=['brizer_state'])
def query_brizer_state_message(message):
    # telegram_user_id = message.from_user.id
    # telegram_user_first_name = message.from_user.first_name
    # message_from_telegram_user = message.text
    # command_for_mqtt = None
    # time_sent_to_topic = None
    # sent_to_mqtt_topic = None
    # time_received_from_topic = None
    # received_from_mqtt_topic = None
    # message_received_from_mqtt_topic = None
    # command_execution_duration = None
    if message.from_user.id == 1423529490:
        # markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=False)
        # itembtn1 = types.KeyboardButton(door_open_command)
        # itembtn2 = types.KeyboardButton(domofon_code_command)
        # markup.add(itembtn1, itembtn2)
        # time_sent_to_topic = datetime.datetime.now().replace(microsecond=0)
        brizer_state = read_last_line_from_db()
        print('Record from DB in Telebot decorator: ', brizer_state)
        if brizer_state:
            # if brizer_state[1] == 'tion_4s':
            #     brizer_name = 'Tion 4S'

            if brizer_state[2] == 'fan_only':
                brizer_mode = '❄'
            elif brizer_state[2] == 'off':
                brizer_mode = '💤'
            elif brizer_state[2] == 'heat':
                brizer_mode = '☀'
            else:
                brizer_mode = brizer_state[2]

            # if brizer_state[4] == 0:
            #     recirculation = '➡'
            # elif brizer_state[4] == 1:
            #     recirculation = '🔁'
            # else:
            #     recirculation = brizer_state[2]

            # How old the brizer status is
            delta = datetime.datetime.now() - brizer_state[0]

            if delta.days != 0:
                last_updated = f'{delta.days} дн. назад'
            elif delta.days == 0:
                hours = delta.seconds // 3600  # Got hours
                if hours != 0:
                    last_updated = f'{hours} ч. назад'
                elif hours == 0:
                    minutes = delta.seconds // 60  # Got minutes
                    if minutes != 0:
                        last_updated = f'{minutes} мин. назад'
                    elif minutes == 0:
                        last_updated = f'{delta.seconds} сек. назад'  # Got seconds

            msg_brizer_state = f'''{brizer_state[9]} ppm ({last_updated})

{brizer_mode}{'(' + (str(brizer_state[7]) + 'Вт)') if brizer_state[2] == 'heat' else ''}  {'🔁  ' if brizer_state[4] == 1 else ''}{brizer_speed_dict[brizer_state[3]] if brizer_state[2] != 'off' else ''} 

{brizer_state[5]} ▶ {brizer_state[6]} ▶ {brizer_state[10]} {degree_sign}С'''
            print('ok')
            bot.send_chat_action(message.chat.id, 'typing')
            time.sleep(1)
            # bot.send_message(message.chat.id, msg_brizer_state, reply_markup=markup)
            bot.send_message(message.chat.id, msg_brizer_state)
        else:
            bot.send_chat_action(message.chat.id, 'typing')
            time.sleep(1)
            # bot.send_message(message.chat.id, failed_to_read_from_db, reply_markup=markup)
            bot.send_message(message.chat.id, failed_to_read_from_db)
    else:
        bot.send_chat_action(message.chat.id, 'typing')
        time.sleep(1)
        bot.send_message(message.chat.id, unknown_user)
    # Write send and received (if any) messages info to database
    # write_to_db(telebot_id,
    #             telegram_user_id,
    #             telegram_user_first_name,
    #             message_from_telegram_user,
    #             command_for_mqtt,
    #             time_sent_to_topic,
    #             sent_to_mqtt_topic,
    #             time_received_from_topic,
    #             received_from_mqtt_topic,
    #             msg_brizer_state,
    #             command_execution_duration)


@bot.message_handler(content_types=['text'])
def send_new_message(message):
    telegram_user_id = message.from_user.id
    telegram_user_first_name = message.from_user.first_name
    message_from_telegram_user = message.text
    command_for_mqtt = None
    time_sent_to_topic = None
    sent_to_mqtt_topic = None
    time_received_from_topic = None
    received_from_mqtt_topic = None
    message_received_from_mqtt_topic = None
    command_execution_duration = None
    if message.from_user.id in allowed_teleusers:
        markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=False)
        itembtn1 = types.KeyboardButton(door_open_command)
        itembtn2 = types.KeyboardButton(domofon_code_command)
        markup.add(itembtn1, itembtn2)
        if message.text.lower() == door_open_command.lower() or message.text.lower() == 'открыть':
            send_to_mqtt()  # Sending command to ESP topic
            command_for_mqtt = command_open
            time_sent_to_topic = datetime.datetime.now().replace(microsecond=0)
            sent_to_mqtt_topic = topic_pub
            receive_from_mqtt()  # Waiting for an answer from ESP that command was executed
            if msg1 == [b'Received']:
                time_received_from_topic = datetime.datetime.now().replace(microsecond=0)
                command_execution_duration = (time_received_from_topic - time_sent_to_topic).seconds
                received_from_mqtt_topic = topic_sub
                message_received_from_mqtt_topic = str(msg1[0])
                print('ok')
                msg1[0] = 0  # Set msg1 to default to avoid error in this IF
                bot.send_chat_action(message.chat.id, 'typing')
                time.sleep(1)
                bot.send_message(message.chat.id, door_open_success, reply_markup=markup)
            else:
                bot.send_chat_action(message.chat.id, 'typing')
                time.sleep(1)
                bot.send_message(message.chat.id, door_open_no_response, reply_markup=markup)

        elif message.text.lower() == domofon_code_command.lower():
            time_sent_to_topic = datetime.datetime.now().replace(microsecond=0)
            bot.send_chat_action(message.chat.id, 'typing')
            time.sleep(1)
            bot.send_message(message.chat.id, domofon_code_key, reply_markup=markup)

        else:
            time_sent_to_topic = datetime.datetime.now().replace(microsecond=0)
            bot.send_message(admin_teleuser_id,
                             message.from_user.first_name + ' отправил сообщение: "' + message.text + '"')
            bot.send_chat_action(message.chat.id, 'typing')
            time.sleep(1)
            bot.send_message(message.chat.id, 'Это что было? 🙄🤪', reply_markup=markup)
            time.sleep(2)
            bot.send_chat_action(message.chat.id, 'typing')
            time.sleep(1)
            bot.send_message(message.chat.id, 'Жми /open или "Открыть дверь" \n👇👇👇', reply_markup=markup)
            time.sleep(2)
            bot.send_chat_action(message.chat.id, 'typing')
            time.sleep(1)
            bot.send_message(message.chat.id, 'Ну или напиши Открыть 😉', reply_markup=markup)
            time.sleep(2)
            bot.send_chat_action(message.chat.id, 'typing')
            time.sleep(1)
            bot.send_message(message.chat.id, 'Но только не "' + message.text + '"!', reply_markup=markup)
    else:
        bot.send_chat_action(message.chat.id, 'typing')
        time.sleep(1)
        bot.send_message(message.chat.id, unknown_user)
    # Write send and received (if any) messages info to database
    write_to_db(telebot_id,
                telegram_user_id,
                telegram_user_first_name,
                message_from_telegram_user,
                command_for_mqtt,
                time_sent_to_topic,
                sent_to_mqtt_topic,
                time_received_from_topic,
                received_from_mqtt_topic,
                message_received_from_mqtt_topic,
                command_execution_duration)



# Removing webhook before next setup (helps to solve some issues)
bot.remove_webhook()

# Setup webhook once again
bot.set_webhook(url=WEBHOOK_URL_BASE + WEBHOOK_URL_PATH,
                certificate=open(WEBHOOK_SSL_CERT, 'r'))

# Settings for Cherry server
cherrypy.config.update({
    'server.socket_host': WEBHOOK_LISTEN,
    'server.socket_port': WEBHOOK_PORT,
    'server.ssl_module': 'builtin',
    'server.ssl_certificate': WEBHOOK_SSL_CERT,
    'server.ssl_private_key': WEBHOOK_SSL_PRIV
})

# Launch of Cherry server
cherrypy.quickstart(WebhookServer(), WEBHOOK_URL_PATH, {'/': {}})
