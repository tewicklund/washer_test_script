from funcs import *

# is power analyzer even connected?
xitron_present=False

# power analyzer constants
xitron_ip='192.168.99.7'
xitron_port='10733'


# user input
test_name=input("Enter name for test: ")
test_duration=int(input("Enter test duration in seconds: "))
seconds_between_samples=1
num_samples=int(test_duration/seconds_between_samples)     
num_harms=15
samples_btwn_ambi=10


# port info lists
table_names=["Cold Temp.","Hot Temp.","Cold Pres.","Hot Pres.","Cold Flow","Hot Flow","Near Ambi.","Far Ambi"]
get_unit_functions=[get_cold_temp_unit,get_hot_temp_unit,get_cold_pres_unit,get_hot_pres_unit,get_cold_flow_unit,get_hot_flow_unit,get_temp_rh_near_unit,get_temp_rh_far_unit]
get_value_functions=[get_cold_temp_value,get_hot_temp_value,get_cold_pres_value,get_hot_pres_value,get_cold_flow_value,get_hot_flow_value,get_temp_rh_near_value,get_temp_rh_far_value]
column_headers='sample_num,epoch_timestamp_ms,human_timestamp,'
near_cache="75.9 : 41.6"
far_cache="75.9 : 41.6"



# compute output file name
log_file_name=test_name+"_"+str(int(time.time()*1000))+".csv"

# get power analyzer query string with harmonics
xitron_q_string=""
with open('ch1_q_string.txt') as query_file:
    xitron_q_string=query_file.readline()
for x in range(num_harms):
    xitron_q_string+=f',V:CH1:H{x+1},A:CH1:H{x+1}'
xitron_q_string+='\n'
print(f'query string: {repr(xitron_q_string)}')

# create log file and add headers
with open(log_file_name,'w') as log_file:
    log_file.write('sample_num,epoch_timestamp_ms,human_timestamp,')
    for port_num in range(8):
        requested_unit=get_unit_functions[port_num]()
        if requested_unit=='offline':
            requested_unit='???'
        log_file.write(f'{table_names[port_num]} ({requested_unit}),')
        time.sleep(0.1)
    log_file.write(xitron_q_string.removeprefix('READ?,').rstrip('\r\n'))
    log_file.write('\n')


# prep power analyzer for logging
if xitron_present:
    xitron_socket=socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    xitron_socket.connect((xitron_ip,int(xitron_port)))
    xitron_socket.settimeout(1)


# main test loop
for sample_num in range(num_samples):

    # collect timestamp info
    sample_time=time.time()
    timestamp_data=make_timestamp(sample_num)
    requested_values=[]

    # collect data from io-link hub
    if (sample_num%samples_btwn_ambi==0):
        for port_num in range(8):
            requested_value=get_value_functions[port_num]()
            requested_values.append(requested_value)
        near_cache=requested_values[6]
        far_cache=requested_values[7]
    else:
        for port_num in range(6):
            requested_value=get_value_functions[port_num]()
            requested_values.append(requested_value)
        requested_values.append(near_cache)
        requested_values.append(far_cache)

    # collect data from power analyzer
    if xitron_present:
        xitron_socket.sendall(xitron_q_string.encode())
        response_string=xitron_socket.recv(4096).decode().rstrip('\r\n')

    # write data from both to log
    with open(log_file_name,'a') as log_file:
        log_file.write(f" {timestamp_data['sample_num']} , {timestamp_data['epoch_timestamp_ms']} , {timestamp_data['human_timestamp']},")
        for port_num in range(8):
            log_file.write(f'{requested_values[port_num]},')

        if xitron_present:
            log_file.write(response_string)
        #print(f'response: {repr(response_string)}')
        log_file.write('\n')

    # wait till next second to log next data point
    end_time=time.time()
    if end_time-sample_time>int(seconds_between_samples):
        print(f"warning: logging too fast: {end_time-sample_time} seconds to log this point")

    while time.time()-sample_time<int(seconds_between_samples):
        pass