from enum import Enum
import logging
import telegram
import transitfeed

LAST_UPDATE_ID = None
schedule = None

# keeps dictionary of 
# user_id : UserState
current_state_by_user = {}

# keeps dictionary of 
# user_id: Query
current_query_by_user = {}

stops = []

# Any given user Query. We fill this in as we ask questions
class Query:
    departure_stop_id = 0
    arrival_stop_id = 0
    departing_times = []
    arriving_times = []
    is_weekend = False

    def __init__(self):
        self.departure_stop_id = 0

# The `model` for a given stop
class Stop:
    stop_id = 0
    stop_name = ""

    def __init__(self, stop_id, stop_name):
        self.stop_id = stop_id
        self.stop_name = stop_name

# The state any given user can be in
class UserState(Enum):
    undefined = 1
    asked_departure = 2
    asked_arrival = 3
    asked_weekday = 4
    asked_time = 5

def main():
    global LAST_UPDATE_ID

    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Telegram Bot Authorization Token
    bot = telegram.Bot('<auth token>')

    # Process!
    process_times()

    # This will be our global variable to keep the latest update_id when requesting
    # for updates. It starts with the latest update_id if available.
    try:
        LAST_UPDATE_ID = bot.getUpdates()[-1].update_id
    except IndexError:
        LAST_UPDATE_ID = None

    while True:
        run(bot)

def process_times():
    global schedule
    global stops

    # Load the GTFS file
    input_path = "/home/ec2-user/GTFS-Caltrain"
    loader = transitfeed.Loader(input_path)

    # Get schedule
    schedule = loader.Load()

    # Get stops 
    for stop_id, stop in schedule.stops.items():
        if str(stop_id).endswith("2"):
            stops.append(Stop(stop_id, stop.stop_name))

# Converts (Seconds from midnight) -> HH:MM (AM/PM)
def time_to_stamp(seconds):
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if (h > 12):
        h = h - 12
        return "%d:%02d PM" % (h, m)
    return "%d:%02d AM" % (h, m)

# [List] to [[List] [List]...] 
def split_list(alist, wanted_parts=4):
    length = len(alist)
    return [ alist[i*length // wanted_parts: (i+1)*length // wanted_parts] 
             for i in range(wanted_parts) ]


# Given a departure_stop_id and an arrival_stop_id,
# fetches the departing and arrival times from departure -> arrival
def get_times(from_stop_id, to_stop_id, weekend_only):
    global schedule

    # when we are going north, the data has different stop_ids. 
    # in fact, they are the same except the last number is '1'
    from_stop_id = str(from_stop_id)
    to_stop_id = str(to_stop_id)

    # Northmost stops have the lowest stop_id, so if this
    # is the case, we know we are going north. We need to change
    # the stop numbers to reflect that
    if from_stop_id > to_stop_id:
        from_stop_id = list(from_stop_id)
        from_stop_id[len(from_stop_id) - 1] = '1'
        from_stop_id = ''.join(from_stop_id)
        to_stop_id = list(to_stop_id)
        to_stop_id[len(to_stop_id) - 1] = '1'
        to_stop_id = ''.join(to_stop_id)

    departing_times = []
    arrival_times = []

    for departing_stop_id, departing_stop in schedule.stops.items():
        if departing_stop_id == from_stop_id:
            for departure_time, (trip, index), is_timepoint in departing_stop.GetStopTimeTrips():
                for arrival_time, x , arriving_stop in trip.GetTimeStops():
                    if arrival_time > departure_time and arriving_stop.stop_id == to_stop_id:
                        if "Weekday" in trip.trip_id:
                            if not weekend_only:
                                departing_times.append(time_to_stamp(departure_time))
                                arrival_times.append(time_to_stamp(arrival_time))
                        else:
                            if weekend_only:
                                departing_times.append(time_to_stamp(departure_time))
                                arrival_times.append(time_to_stamp(arrival_time))

    return departing_times, arrival_times

def run(bot):
    global LAST_UPDATE_ID
    global stops
    global current_state_by_user
    global current_query_by_user

    # Request updates after the last updated_id
    for update in bot.getUpdates(offset=LAST_UPDATE_ID, timeout=10):
        # chat_id is required to reply any message
        chat_id = update.message.chat_id
        message = update.message.text.encode('utf-8')
        user_id = update.message.from_user.id

        if (message):
            # Wake up, Bot!
            if "/train" in message and current_state_by_user.get(user_id, UserState.undefined) == UserState.undefined:
                # Update User State
                current_state_by_user[user_id] = UserState.asked_departure

                # Return all possible stops and ask about departure
                custom_keyboard = split_list([stop.stop_name for stop in stops], wanted_parts = 20)
                reply_markup = telegram.ReplyKeyboardMarkup(custom_keyboard, resize_keyboard = True)
                bot.sendMessage(chat_id=chat_id,
                            text="Where are you departing from?", reply_markup = reply_markup)
            # Once we have a departure stop, ask about arrival
            elif current_state_by_user.get(user_id, UserState.undefined) == UserState.asked_departure:
                user_query = Query()
                user_query.departure_stop_id = next((x for x in stops if x.stop_name == message), None).stop_id

                current_query_by_user[user_id] = user_query
                current_state_by_user[user_id] = UserState.asked_arrival
                custom_keyboard = split_list([stop.stop_name for stop in stops], wanted_parts = 20)
                reply_markup = telegram.ReplyKeyboardMarkup(custom_keyboard, resize_keyboard = True)
                bot.sendMessage(chat_id = chat_id, text = "Where are you going?", reply_markup = reply_markup)

            elif current_state_by_user.get(user_id, UserState.undefined) == UserState.asked_arrival:
                user_query = current_query_by_user[user_id]
                user_query.arrival_stop_id = next((x for x in stops if x.stop_name == message), None).stop_id

                current_state_by_user[user_id] = UserState.asked_weekday
                custom_keyboard = [["Weekday", "Weekend"]]

                reply_markup = telegram.ReplyKeyboardMarkup(custom_keyboard, resize_keyboard = True)
                bot.sendMessage(chat_id = chat_id, text = "Weekday, or Weeekend?", reply_markup = reply_markup)

            # Once we have an arrival stop, ask about timing
            elif current_state_by_user.get(user_id, UserState.undefined) == UserState.asked_weekday:
                # Get the current user's query, and add this new information
                user_query = current_query_by_user[user_id]
                if "Weekday" in message:
                    user_query.is_weekend = False
                else:
                    user_query.is_weekend = True

                # about to ask for time
                current_state_by_user[user_id] = UserState.asked_time

                # Get and Store the list of departing and corresponding arrival times
                user_query.departing_times, user_query.arriving_times = get_times(user_query.departure_stop_id, user_query.arrival_stop_id, user_query.is_weekend)

                custom_keyboard = split_list(user_query.departing_times, wanted_parts = 10)
                reply_markup = telegram.ReplyKeyboardMarkup(custom_keyboard, resize_keyboard = True, one_time_keyboard = True)
                bot.sendMessage(chat_id=chat_id,
                            text="When will you leave?", reply_markup = reply_markup)
            # Return when it will get there
            elif current_state_by_user.get(user_id, UserState.undefined) == UserState.asked_time:
                user_query = current_query_by_user[user_id]
                current_state_by_user[user_id] = UserState.undefined
                index = user_query.departing_times.index(message)
                custom_keyboard = telegram.ReplyKeyboardHide()
                reply_markup = telegram.ReplyKeyboardMarkup(custom_keyboard, one_time_keyboard = True)
                bot.sendMessage(chat_id=chat_id,
                            text="Gets there at " + str(user_query.arriving_times[index]) + "!", reply_markup = reply_markup)
            else:
                current_state_by_user[user_id] = UserState.undefined
                current_query_by_user[user_id] = Query()
                custom_keyboard = telegram.ReplyKeyboardHide()
                reply_markup = telegram.ReplyKeyboardMarkup(custom_keyboard)
                bot.sendMessage(chat_id=chat_id, text="Hm...I didn't understand that. Try typing `/train`")


            # Updates global offset to get the new updates
            LAST_UPDATE_ID = update.update_id + 1


if __name__ == '__main__':
    main()
