from serial import Serial
import znp

port = "COM7"

# Code to interact with a device running the @KoenKK compiled Z-Stack HA 1.2 firmware. This is nominally "coordinator", in which guise is is the
# firmware recommended for Zigbee2MQTT coordinators running on CC2531 USB dongles.

# expectation is that the serial adapter will be live but not necessarily the ZNP device
S = Serial(port=port, baudrate=115200)  # default timeout is forever
S.reset_input_buffer()
# S.close()
# exit(0)

# --------- wait for device
input("Reset or power-up ZNP then hit return. Will wait for SYS_RESET_IND")


print("Waiting")
f1 = znp.ZnpFrameBody(S)
print(f1)

if f1.command == znp.SYS_RESET_IND:
    print("Device ready")

# ------------ Configuration stored to NV memory on device
success = znp.zb_write_configuration(S,
                                     b'\x87',  # ZCD_NV_LOGICAL_TYPE
                                     b'\x02')  # end device
if success:
    success = znp.zb_write_configuration(S,
                                         b'\x83',  # ZCD_NV_PAN_ID
                                         b'\xff\xff')  # "dont care" - tolerate whatever the coordinator has. Expected to be OK if only one PAN.
# could set the channel mask here - use ZCD_NV_CHANLIST - but presume that the firmware defaults will be suitable (same firmware as coordinator)
# For tinkering, set ZCD_NV_STARTUP_OPTION to cause clear network state on restart. Otherwise the device will attempt to resume its network, which might be bollocks.
if success:
    success = znp.zb_write_configuration(S,
                                         b'\x03',  # ZCD_NV_STARTUP_OPTION
                                         b'\x02')  # clear NV memory. Also possible to clear config settings.
if not success:
    raise Exception("FAILED to write device basic configuration")

# ----------- issue a ZB_SYSTEM_RESET command to clear state
print("Resetting to clear network state.")
znp.command_no_data(S, znp.ZB_SYSTEM_RESET)  # ignore response frame (but this DOES wait for it)
print("Reset complete.")

# ---------- Register the device and define the clusters. There is an alternative command in the Simple API
znp.af_register(S,
                endpoint=b'\x01',  # A single endpoint is being used, 1
                app_prof_id=b'\x01\x04',  # Home Automation Profile (prescribed) = 260 decimal
                app_device_id=b'\x00\x00',  # Device ID also in the HA Profile spec - this is the On/Off switch Id
                app_dev_ver=b'\x01',  # I think the device version is not prescribed
                in_cluster_ids=(b'\x00\x00',),  # only Basic Cluster, which is device info such as name, manufacturer etc TODO in, out, or both for Basic?
                print_msg=True
                )

# ----------- join the network, waiting for the device state to become DEV_END_DEVICE
# there are 2 options (and it is to be determined whether these can be mixed (simple/not) with the register api call used:
# - ZB_START_REQUEST (0x2600) [Simple API], which takes no parameters and returns a plain (no data) ZB_START_REQUEST_RSP (0x6600) immediately, and
# - ZDO_STARTUP_FROM_APP, which takes a parameter and returns a RSP with a network state byte
input("MAKE SURE Zigbee2MQTT is accepting join requests and then hit any key. (Otherwise you get status=2 INVALID_PARAMETER from ZDO_STATE_CHANGE_IND)")
fr = znp.send_and_await_response(S,
                                 znp.append_fcs(b'\xfe\x01\x25\x40\x00'),  # ZDO_STARTUP_FROM_APP with 0 delay
                                 print_msg=True)  # this should cause a response 0x6540 with network state code + multiple ZDO_STATE_CHANGE_IND
device_ready = False
while not device_ready:  # TODO put a loop count and exit
    if fr.command == b'\x65\x40':
        print("Startup response: network status code = {}".format(fr.data[0].to_bytes(length=1, byteorder="big")))
    elif fr.command == b'\x45\xC0':  # ZDO_STATE_CHANGE_IND
        state = fr.data[0]
        if state != 2:
            print(f"State = {state}")
        device_ready = state == 6  # DEV_END_DEVICE
    else:
        print("Recd cmd: {}".format(fr.command.hex()))
    if not device_ready:
        fr = znp.ZnpFrameBody(S)  # wait for and get next frame, but NOT if the status is terminal = device is ready
print("Joined coordinator network.")

# ------------- THIS IS WHERE Z2M Starts its interview, issuing multiple AF_INCOMING_MSG
running = True
while running:
    # check for incoming messages which correspond to the Z2M "interview"
    if S.inWaiting():
        f = znp.ZnpFrameBody(S)
        in_msg = znp.AfIncomingMessage(f)
        if in_msg.is_af_incoming_message:
            print(in_msg.zcl_raw.hex(" ", 1))
            # TODO should probably check cluster ID AND ZCL command id and respond here. the incoming message may ask for one attribute or several,
            # and the response should bundle accordingly
        else:
            print("Not AF_INCOMING_MSG")

S.close()
