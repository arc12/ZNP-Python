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
                                     b'\x02',  # end device
                                     print_msg=True)
if success:
    success = znp.zb_write_configuration(S,
                                         b'\x83',  # ZCD_NV_PAN_ID
                                         b'\xff\xff',  # "dont care" - tolerate whatever the coordinator has. Expected to be OK if only one PAN.
                                         print_msg=True)
# could set the channel mask here - use ZCD_NV_CHANLIST - but presume that the firmware defaults will be suitable (same firmware as coordinator)
# For tinkering, set ZCD_NV_STARTUP_OPTION to cause clear network state on restart. Otherwise the device will attempt to resume its network, which might be bollocks.
if success:
    success = znp.zb_write_configuration(S,
                                         b'\x03',  # ZCD_NV_STARTUP_OPTION
                                         b'\x02',  # clear NV memory. Also possible to clear config settings.
                                         print_msg=True)

# >>> this modifies the ZCD_NV_POLL_RATE, which controls the interval with which the device contacts the coordinator with IEEE 802.15.4 "Data Request" packets
# (see Wireshark sniffer). The compiled default is 1000ms. THe documentation wrongly states this is config_id 0x24 and has byte length. It is actually
# config_id = 0x35 and is a 4 byte value (in ms) expressed in little-endian form
if success:
    success = znp.zb_write_configuration(S,
                                         b'\x35',  # ZCD_NV_POLL_RATE
                                         b'\x98\x3A\x00\x00',  # 15,000ms, as PTVO, little-endian
                                         print_msg=True)

if not success:
    raise Exception("FAILED to write device basic configuration")

# ----------- issue a ZB_SYSTEM_RESET command to clear state
print("Resetting to clear network state.")
znp.command_no_data(S, znp.ZB_SYSTEM_RESET)  # ignore response frame (but this DOES wait for it)
print("Reset complete.")

# ---------- Register the device and define the clusters. There is an alternative command in the Simple API. IDs here are big-endian
znp.af_register(S,
                endpoint=b'\x01',  # A single endpoint is being used, 1
                app_prof_id=b'\x01\x04',  # Home Automation Profile (prescribed) = 260 decimal
                app_device_id=b'\x00\x00',  # Device ID also in the HA Profile spec - this is the On/Off switch Id
                app_dev_ver=b'\x01',  # I think the device version is not prescribed
                in_cluster_ids=(  # this is a tuple
                    b'\x00\x00',  # Basic Cluster, which is device info such as name, manufacturer etc
                    b'\x00\x06'  # On/Off LED - the messaging is IN to the device, which is therefore a "server"
                    # switch config would go here (PTVO switches have this)
                ),
                out_cluster_ids=(  # this is a tuple
                    # PTVO GPIO LED also has an out cluster id 0x0006, which reports its state periodically (interval set in PTVO app), and this would
                    # presumably cover the case where there is an on/off switch on the device itself as well as remote control of the LED
                    b'\x00\x06',
                    #  b'\x00\x12',  # On/Off switch is in output cluster list and so is a "client". This is actually a "Multistate". Plain on/off would be 0x0006
                ),
                print_msg=True
                )

# ----------- join the network, waiting for the device state to become DEV_END_DEVICE
# there are 2 options (and it is to be determined whether these can be mixed (simple/not) with the register api call used:
# - ZB_START_REQUEST (0x2600) [Simple API], which takes no parameters and returns a plain (no data) ZB_START_REQUEST_RSP (0x6600) immediately, and
# - ZDO_STARTUP_FROM_APP, which takes a parameter and returns a RSP with a network state byte
input("MAKE SURE Zigbee2MQTT is accepting join requests and then hit any key. (Otherwise you get status=2 INVALID_PARAMETER from ZDO_STATE_CHANGE_IND)")
fr = znp.send_and_await_response(S,
                                 b'\x01\x25\x40\x00',  # ZDO_STARTUP_FROM_APP with 0 delay
                                 print_msg=True)  # this should cause a response 0x6540 with network state code + multiple ZDO_STATE_CHANGE_IND
device_ready = False
state_2_count = 0
while not device_ready:
    if fr.command == b'\x65\x40':
        print("Startup response: network status code = {}".format(fr.data[0].to_bytes(length=1, byteorder="big")))
    elif fr.command == b'\x45\xC0':  # ZDO_STATE_CHANGE_IND
        state = fr.data[0]
        if state == 2:
            state_2_count += 1
        else:
            print(f"State = {state}")
        device_ready = state == 6  # DEV_END_DEVICE
        if state_2_count > 20:
            raise Exception("Excessive state=2 from ZDO_STATE_CHANGE_IND. Coordinator probably not accepting joins or off-line.")
    else:
        print("Recd cmd: {}".format(fr.command.hex()))
    if not device_ready:
        fr = znp.ZnpFrameBody(S)  # wait for and get next frame, but NOT if the status is terminal = device is ready
print("Joined coordinator network.")

# ------------- THIS IS WHERE Z2M Starts its interview, issuing multiple AF_INCOMING_MSG
running = True
# set up for reporting events at 10s intervals
from time import time
last_time_toggle = int(time() // 10) % 2
# device state variables
device_output_onoff = False  # LED off
while running:
    # check for incoming messages which correspond to the Z2M "interview"
    if S.inWaiting():
        f = znp.ZnpFrameBody(S)
        print("RX body:", f)
        # first trap informative stuff (at least as far as we are treating things)
        if f.command == b'\x44\x80':  # AF_DATA_CONFIRM
            print("AF_DATA_CONFIRM for TransId={} on Endpoint={} had Status={}".format(f.data[2], f.data[1], f.data[0]))
            continue

        in_msg = znp.AfIncomingMessage(f)
        if in_msg.is_af_incoming_message:
            print("Incoming ZCL for endpoint {}, cluster id = {} is: {}".format(in_msg.dst_endpoint[0],
                                                                                in_msg.cluster_id.hex(),
                                                                                in_msg.zcl_raw.hex(" ")))
            if in_msg.zcl.zcl_command == b'\x00':  # Read Attributes
                if in_msg.cluster_id == b'\x00\x00':
                    cluster_provider = znp.BasicClusterAttributeParts(model_identifier="ZNP-Test")
                else:
                    cluster_provider = None

                if cluster_provider is None:
                    print("Unsupported cluster {}; cannot respond".format(in_msg.cluster_id.hex()))
                data = znp.ZclFrameReadAttributesResponse(response_to=in_msg.zcl,
                                                          cluster_provider=znp.BasicClusterAttributeParts(model_identifier="ZNP-Test")).zcl_message()
                # AF_DATA_REQUEST 0x2401
                print("Sending response...")
                out_msg = (10 + len(data)).to_bytes(length=1, byteorder="big") + b'\x24\x01' + \
                    in_msg.src_addr + in_msg.src_endpoint + in_msg.dst_endpoint + in_msg.cluster_id + in_msg.transaction_seq_no + b'\x00' + b'\x10' +\
                    len(data).to_bytes(length=1, byteorder="big") + data
                rsp_success = znp.send_and_check_success(S, out_msg, b'\x64\x01', print_msg=True)
                print("AF_DATA_REQUEST_RSP success?", rsp_success)  # ALSO likely to be AF_DATA_CONFIRM for request, in addition to AF_DATA_REQUEST response
        else:
            print("Unexpected message")
            print("RX body:", f)

            # *** this is how PTVO LED reports its state, which it does on report period (not when state externally changed)
            # ZigBee Network Layer Data, Dst: 0x0000, Src: 0xb85a
            # ZigBee Application Support Layer Data, Dst Endpt: 1, Src Endpt: 1
            #     Frame Control Field: Data (0x00)
            #     Destination Endpoint: 1
            #     Cluster: On/Off (0x0006)
            #     Profile: Home Automation (0x0104)
            #     Source Endpoint: 1
            #     Counter: 8
            # ZigBee Cluster Library Frame, Command: Report Attributes, Seq: 2
            #     Frame Control Field: Profile-wide (0x08)
            #     Sequence Number: 2
            #     Command: Report Attributes (0x0a)
            #     Attribute Field
            #         Attribute: OnOff (0x0000)
            #         Data Type: Boolean (0x10)
            #         On/off Control: Off (0x00)

    # output event?
    time_toggle = int(time() // 10) % 2
    if time_toggle != last_time_toggle:
        last_time_toggle = time_toggle
        print("do event...")
        device_output_onoff = not device_output_onoff

S.close()


