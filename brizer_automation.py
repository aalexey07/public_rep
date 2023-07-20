# Скрипт для автоматизации работы приточной системы (бризер Tion 4s) в зависимости от различных условий
# Работает соместно с:
# - платой ESP32 (см. файл концигурации ESPHOME tion-4s.yaml) - подключена к бризеру по BLE, к СО2 по I2C
# - ботом в телеграмме (см. telebot.py) - получение последнего известного состояния бризера

import time

from paho.mqtt import client as mqtt_client
from brizer_config import *
import psycopg2
from psycopg2 import Error
import datetime
import telebot

bot = telebot.TeleBot(token, parse_mode=None)
notification_counter = 0    # Message that brizer OFF was sent (1) or not (0)

# brizer_state:
# 0 - mode_state
# 1 - fan_mode_state
# 2 - recirculation
# 3 - outdoor_temperature
# 4 - current_temperature_state
# 5 - heater_power
# 6 - total_airflow
# 7 - zyaura_co2
# 8 - zyaura_temp
# 9 - target_temp
brizer_state = ['', 0, 0, 0, 0, 0, 0, 0, 0, 0]

brizer_previous_state = brizer_state.copy()

# Brizer fan speed according to CO2 level to avoid sending new command if user changed fan speed
co2_status = ''

# To avoid frequent fan speed changes [co2 level, total measures]
number_of_co2_measurements = ['', 0]

# Brizer automation modes example:
# [0 <= CO2 < 500 ppm, brizer fan speed is 1, co2 status is White]
# Starting from 100 ppm to avoid false fan speed change if no data from co2 sensor

# Low speed mode
# brizer_mode = [[100, 550, '1', 'White'],
#                [550, 750, '2', 'Green'],
#                [750, 800, '3', 'Yellow'],
#                [800, 900, '4', 'Orange'],
#                [900, 1100, '5', 'Red'],
#                [1100, 10000, '6', 'Boost']]

# High speed mode
brizer_mode = [[100, 550, '1', 'White'],
               [550, 600, '2', 'Green'],
               [600, 650, '3', 'Yellow'],
               [650, 700, '4', 'Orange'],
               [700, 750, '5', 'Red'],
               [750, 10000, '6', 'Boost']]

# Brizer parameters parser from topics and definitions of messages:
brizer_parameters = {0: topic_mode_state,
                     1: topic_fan_mode_state,
                     2: topic_tion_4s,
                     4: topic_current_temperature_state,
                     9: topic_target_temperature_state}

topic_tion_4s_parameters = {2: 'recirculation',
                            3: 'outdoor_temp',
                            5: 'heater_power',
                            6: 'total_airflow',
                            7: 'zyaura_co2',
                            8: 'zyaura_temp'}

# To make less noise at night
night_speed_limit = '4'  # Maximum fan speed at night hours
day_period = [8, 22]  # In hours

# To switch between fan/heat modes
higher_temp_for_fan = 18        # If outdoor temp higher -> switch to fan mode
lower_temp_for_heat = 16        # If outdoor temp lower -> switch to heat mode

# To lower heater consumption
winter_target_temperature_away = '5'
winter_target_temperature_home = '18'


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


def subscribe(client: mqtt_client):
    def on_message(client, userdata, msg):
        global brizer_state
        global brizer_previous_state
        global co2_status
        global number_of_co2_measurements
        global brizer_mode
        global brizer_parameters
        global topic_tion_4s_parameters
        global notification_counter

        try:
            for m in brizer_parameters:
                if m == 9 and msg.topic == brizer_parameters[m]:
                    brizer_state[m] = int(msg.payload.decode())

                    # Analyze breezer mode to send notification into telegram
                    if brizer_state[0] == 'off' and notification_counter < 2:
                        notification_counter += 1
                    elif brizer_state[0] == 'off' and notification_counter >= 2:
                        bot.send_message(admin_teleuser_id, 'Бризер выключен!')
                        print('Sent message to telebot')
                    elif brizer_state[0] != 'off':
                        notification_counter = 0

                    # Analyze outdoor temperature and CO2 level to adjust heating if brizer in heat mode
                    # If day and fan speed == 1 and target_temperature > away temperature level:
                    if brizer_state[0] == 'heat' and brizer_state[9] != 0:
                        if datetime.datetime.now().hour in range(*day_period) and co2_status == \
                                brizer_mode[0][3] and brizer_state[9] > int(winter_target_temperature_away):
                            result = client.publish(topic_target_temperature_command, winter_target_temperature_away)
                            status = result[0]
                            if status == 0:
                                print(
                                    f'Sent target temperature "{winter_target_temperature_away}" to topic "{topic_target_temperature_command}"')
                            else:
                                print(f"Failed to send message to topic {topic_target_temperature_command}")

                        # If fan speed != 1 and target_temperature < home target level:
                        elif co2_status != brizer_mode[0][3] and brizer_state[9] < int(winter_target_temperature_home):
                            result = client.publish(topic_target_temperature_command, winter_target_temperature_home)
                            status = result[0]
                            if status == 0:
                                print(
                                    f'Sent target temperature "{winter_target_temperature_home}" to topic "{topic_target_temperature_command}"')
                            else:
                                print(f"Failed to send message to topic {topic_target_temperature_command}")
                    break
                if m != 2 and m != 0 and msg.topic == brizer_parameters[m]:
                    brizer_state[m] = int(msg.payload.decode())
                    break
                if m == 0 and msg.topic == brizer_parameters[m]:
                    brizer_state[m] = str(msg.payload.decode())
                    break
                elif m == 2 and msg.topic == brizer_parameters[m]:
                    message = str(msg.payload.decode())
                    temp = message.split(':')
                    for k in topic_tion_4s_parameters:
                        if temp[0] == topic_tion_4s_parameters[k]:
                            brizer_state[k] = int(temp[1].partition('.')[0])
                            break
        except Exception as e:
            print('Exception: ', e)
        print(f"\nReceived `{msg.payload.decode()}` from `{msg.topic}` topic")
        print('Brizer state: ', brizer_state)
        print('Brizer previous state: ', brizer_previous_state)

        # Analyze brizer state to change fan speed and/or commit to psql
        # 0 - If brizer mode didn't change
        # 1 - If fan mode didn't change
        # 2 - If recirculation mode didn't change
        # 3 - If outdoor temperature didn't change more, than 2 degrees
        # 4 - If brizer temperature didn't change more, than 2 degrees
        # 5 - not used for control: If heater power didn't change more, than 2 wt (
        #                 brizer_state[5] not in range(brizer_previous_state[5] - 1, brizer_previous_state[5] + 2)) or
        # 6 - not used for control
        # 7 - If CO2 level didn't change more, than 15 ppm
        # 8 - If external sensor temperature didn't change more, than 2 degrees
        # 9 - Target temperature for heating (not used for control)
        if (brizer_state[0] != brizer_previous_state[0]) or (brizer_state[1] != brizer_previous_state[1]) or (
                brizer_state[2] != brizer_previous_state[2]) or (
                brizer_state[3] not in range(brizer_previous_state[3] - 1, brizer_previous_state[3] + 2)) or (
                brizer_state[4] not in range(brizer_previous_state[4] - 2, brizer_previous_state[4] + 3)) or (
                brizer_state[7] not in range(brizer_previous_state[7] - 3, brizer_previous_state[7] + 4)) or (
                brizer_state[8] not in range(brizer_previous_state[8] - 1, brizer_previous_state[8] + 2)):

            # Lowering fan speed to night mode
            if datetime.datetime.now().hour not in range(*day_period) and brizer_state[1] > int(night_speed_limit):
                print(f'Fan speed is to high for the night. Will be lowered to "{night_speed_limit}"')
                result = client.publish(topic_fan_mode_command, night_speed_limit)
                status = result[0]
                if status == 0:
                    print(f'Sent fan speed "{night_speed_limit}" to topic "{topic_fan_mode_command}"')
                    print(
                        f'\n### ## # Brizer fan will be set to "{night_speed_limit}" speed for the night"\n')
                else:
                    print(f"Failed to send message to topic {topic_fan_mode_command}")
                time.sleep(5)

            # Setting the needed fan speed according to CO2 level
            for i in range(len(brizer_mode)):
                if brizer_state[7] in range(brizer_mode[i][0], brizer_mode[i][1]) and co2_status != brizer_mode[i][3]:
                    if number_of_co2_measurements[0] == brizer_mode[i][3] and number_of_co2_measurements[1] >= 5:
                        # At night max fan speed will be according to night_speed_limit, not only by CO2
                        if datetime.datetime.now().hour not in range(*day_period) and (
                                brizer_mode[i][2] > night_speed_limit):
                            fan_speed = night_speed_limit
                        else:
                            fan_speed = brizer_mode[i][2]
                        result = client.publish(topic_fan_mode_command, fan_speed)
                        status = result[0]
                        if status == 0:
                            print(f'Sent fan speed "{fan_speed}" to topic "{topic_fan_mode_command}"')
                            co2_status = brizer_mode[i][3]
                            number_of_co2_measurements[1] = 0
                            print(
                                f'\n### ## # Brizer fan will be set to "{fan_speed}" speed as CO2 is "{co2_status}"\n')
                        else:
                            print(f"Failed to send message to topic {topic_fan_mode_command}")
                        time.sleep(5)
                    else:
                        if number_of_co2_measurements[0] != brizer_mode[i][3]:
                            number_of_co2_measurements[0] = brizer_mode[i][3]
                            number_of_co2_measurements[1] = 0
                        else:
                            number_of_co2_measurements[1] += 1
                    break
                # Erasing number_of_co2_measurements[1] if CO2 level switched to another mode
                # and then returned to the current mode
                elif brizer_state[7] in range(brizer_mode[i][0], brizer_mode[i][1]) and co2_status == brizer_mode[i][3]:
                    number_of_co2_measurements[1] = 0
            print('Brizer state changed')
            print('\n### ## #number_of_co2_measurements', number_of_co2_measurements, '\n')
            timestamp = datetime.datetime.now().replace(microsecond=0)
            source = number_of_co2_measurements[0] + ' ' + str(number_of_co2_measurements[1])
            write_to_db(timestamp,
                        brizer_name,
                        *brizer_state,
                        source)
            brizer_previous_state = brizer_state.copy()
        else:
            print('Brizer state is the same')

        # Analyze outdoor temperature to switch the brizer to heat/fan_only mode
        if brizer_state[3] != 0:        # Because 0 can be a default value
            if brizer_state[3] > higher_temp_for_fan and brizer_state[0] == 'heat':
                result = client.publish(topic_mode_command, 'fan_only')
                status = result[0]
                if status == 0:
                    print(
                        f'Sent "fan_only" mode to topic "{topic_mode_command}"')
                else:
                    print(f"Failed to send message to topic {topic_mode_command}")
                time.sleep(5)
            elif brizer_state[3] < lower_temp_for_heat and brizer_state[0] == 'fan_only':
                result = client.publish(topic_mode_command, 'heat')
                status = result[0]
                if status == 0:
                    print(
                        f'Sent "heat" mode to topic "{topic_mode_command}"')
                else:
                    print(f"Failed to send message to topic {topic_mode_command}")
                time.sleep(5)

        # # Analyze outdoor temperature and CO2 level to adjust heating if brizer in heat mode
        # # If day and fan speed == 1 and target_temperature > away temperature level:
        # if brizer_state[0] == 'heat' and brizer_state[9] != 0:
        #     if datetime.datetime.now().hour in range(*day_period) and co2_status == \
        #             brizer_mode[0][3] and brizer_state[9] > int(winter_target_temperature_away):
        #         result = client.publish(topic_target_temperature_command, winter_target_temperature_away)
        #         status = result[0]
        #         if status == 0:
        #             print(
        #                 f'Sent target temperature "{winter_target_temperature_away}" to topic "{topic_target_temperature_command}"')
        #         else:
        #             print(f"Failed to send message to topic {topic_target_temperature_command}")
        #         time.sleep(5)
        #
        #     # If fan speed != 1 and target_temperature < home target level:
        #     elif co2_status != brizer_mode[0][3] and brizer_state[9] < int(winter_target_temperature_home):
        #         result = client.publish(topic_target_temperature_command, winter_target_temperature_home)
        #         status = result[0]
        #         if status == 0:
        #             print(
        #                 f'Sent target temperature "{winter_target_temperature_home}" to topic "{topic_target_temperature_command}"')
        #         else:
        #             print(f"Failed to send message to topic {topic_target_temperature_command}")
        #         time.sleep(5)

    for n in brizer_parameters:
        client.subscribe(brizer_parameters[n])
    client.on_message = on_message


def run():
    client = connect_mqtt()
    subscribe(client)
    client.loop_forever()


# Function to write into psql database
def write_to_db(
        timestamp,
        brizer_name,
        mode_state,
        fan_mode_state,
        recirculation,
        outdoor_temperature,
        current_temperature_state,
        heater_power,
        total_airflow,
        zyaura_co2,
        zyaura_temp,
        target_temperature,
        source):
    try:
        # Connecting to database
        connection = psycopg2.connect(user=psql_user,
                                      password=psql_password,
                                      host=psql_host,
                                      port=psql_port,
                                      database=psql_database)
        cursor = connection.cursor()
        insert_query = """ INSERT INTO brizer_automation (
            TIMESTAMP,
            BRIZER_NAME,
            MODE_STATE,
            FAN_MODE_STATE,
            RECIRCULATION,
            OUTDOOR_TEMPERATURE,
            CURRENT_TEMPERATURE_STATE,
            HEATER_POWER,
            TOTAL_AIRFLOW,
            ZYAURA_CO2,
            ZYAURA_TEMP,
            TARGET_TEMPERATURE,
            SOURCE) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
        item_tuple = (
            timestamp,
            brizer_name,
            mode_state,
            fan_mode_state,
            recirculation,
            outdoor_temperature,
            current_temperature_state,
            heater_power,
            total_airflow,
            zyaura_co2,
            zyaura_temp,
            target_temperature,
            source)
        cursor.execute(insert_query, item_tuple)
        connection.commit()
        print("Successfully committed to DB")
    except (Exception, Error) as error:
        print("Failed to commit to DB with error:", error)
    finally:
        if connection:
            cursor.close()
            connection.close()
            print('Connection with PSQL closed successfully')
        else:
            print('Failed to close connection with DB')


if __name__ == '__main__':
    run()
