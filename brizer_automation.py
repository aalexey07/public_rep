# Скрипт для автоматизации работы приточной системы воздуха (бризер Tion 4s) в зависимости от различных условий.
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

# Message that brizer is OFF was sent (1) or not (0)
notification_counter = 0

# Brizer fan speed according to CO2 level to avoid sending new command if user changed fan speed
co2_status = ''

# To avoid frequent fan speed changes [co2 level, total measures]
number_of_co2_measurements = ['', 0]

# Text for messages
brizer_mode_off = 'Бризер выключен!'
mqtt_broker_state_connected = 'Connected to MQTT Broker!'
mqtt_broker_state_failed = 'Failed to connect, return code %d\n'

def connect_mqtt():
    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            print(mqtt_broker_state_connected)
        else:
            print(mqtt_broker_state_failed, rc)

    client = mqtt_client.Client(client_id)
    client.username_pw_set(username, password)
    client.on_connect = on_connect
    client.connect(broker, port)
    return client


def subscribe(client: mqtt_client):
    def on_message(client, userdata, msg):
        global brizer_previous_state, notification_counter, co2_status

        # MQTT message parsing
        try:
            mqtt_topics_value = mqtt_topics.get(msg.topic)
            if mqtt_topics_value == 0:
                brizer_state[mqtt_topics_value] = msg.payload.decode()
            elif mqtt_topics_value is not None:
                brizer_state[mqtt_topics_value] = int(msg.payload.decode().partition('.')[0])
        except Exception as e:
            print('Exception: ', e)
        print(f"\nReceived `{msg.payload.decode()}` from `{msg.topic}` topic")
        print('Brizer state: ', brizer_state)
        print('Brizer previous state: ', brizer_previous_state)

        # Analyze breezer mode to send notification into telegram
        if brizer_state[0] == 'off' and notification_counter < 2:
            notification_counter += 1
        elif brizer_state[0] == 'off' and notification_counter == 2:
            bot.send_message(admin_teleuser_id, brizer_mode_off)
            print('Sent message to telebot')
        elif brizer_state[0] != 'off':
            notification_counter = 0

        # Analyze outdoor temperature and CO2 level to adjust heating if brizer is in heat mode
        if brizer_state[0] == 'heat' and brizer_state[9] != 0:
            # If day time and fan speed == 1 and target_temperature > away temperature level:
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

        # Analyze brizer state to change fan speed and/or commit to psql
        # 0 - If brizer mode didn't change
        # 1 - If fan mode didn't change
        # 2 - If recirculation mode didn't change
        # 3 - If outdoor temperature didn't change more, than 2 degrees
        # 4 - If brizer temperature didn't change more, than 2 degrees
        # 5 - not used for control: If heater power didn't change more, than 2 wt (
        #                 brizer_state[5] not in range(brizer_previous_state[5] - 1, brizer_previous_state[5] + 2)) or
        # 6 - not used for control
        # 10 - If CO2 level didn't change more, than 15 ppm
        # 11 - If external sensor temperature didn't change more, than 2 degrees
        # 9 - Target temperature for heating (not used for control)
        if (brizer_state[0] != brizer_previous_state[0]) or (brizer_state[1] != brizer_previous_state[1]) or (
                brizer_state[2] != brizer_previous_state[2]) or (
                brizer_state[3] not in range(brizer_previous_state[3] - 1, brizer_previous_state[3] + 2)) or (
                brizer_state[4] not in range(brizer_previous_state[4] - 2, brizer_previous_state[4] + 3)) or (
                brizer_state[10] not in range(brizer_previous_state[10] - 3, brizer_previous_state[10] + 4)) or (
                brizer_state[11] not in range(brizer_previous_state[11] - 1, brizer_previous_state[11] + 2)):

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
                if brizer_state[10] in range(brizer_mode[i][0], brizer_mode[i][1]) and co2_status != brizer_mode[i][3]:
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
                elif brizer_state[10] in range(brizer_mode[i][0], brizer_mode[i][1]) and co2_status == brizer_mode[i][3]:
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

    for topic in mqtt_topics:
        client.subscribe(topic)
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
        mhz19_co2,
        bme280_temperature,
        bme280_pressure,
        bme280_humidity,
        bh1750_illuminance,
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
            SOURCE,
            MHZ19_CO2,
            BME280_TEMPERATURE,
            BME280_PRESSURE,
            BME280_HUMIDITY,
            BH1750_ILLUMINANCE) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
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
            source,
            mhz19_co2,
            bme280_temperature,
            bme280_pressure,
            bme280_humidity,
            bh1750_illuminance)
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
