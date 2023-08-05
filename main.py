from serial import Serial
from time import time
import znp

port = "COM7"
do_setup = True

# Code to interact with a device running the @KoenKK compiled Z-Stack HA 1.2 firmware. This is nominally "coordinator", in which guise it is the
# firmware recommended for Zigbee2MQTT coordinators running on CC2531 USB dongles.

# expectation is that the serial adapter will be live but not necessarily the ZNP device
S = Serial(port=port, baudrate=115200)  # default timeout is forever
S.reset_input_buffer()


if do_setup:
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
    # set the channel mask here otherwise the preferred channel will be the compiled default, which is ch 11 (only). Coordinator likely to be set to a different
    # channel to avoid WiFi interference. Setting is not essential, since the end device will try other channels until it finds a joinable PAN, but it does
    # speed things up. The value is 4 bytes, as a bit mask with 1 meaning enabled, with the least significant bit being channel 0.
    # i.e. channel 11 only is 0x00000800 (but this must be expressed in little-endian form for the ZNP message)
    if success:
        success = znp.zb_write_configuration(S,
                                             b'\x84',  # ZCD_NV_CHANLIST
                                             # 0b100000000000.to_bytes(4, "little"),  # ch 11
                                             0b10000000000000000.to_bytes(4, "little"),  # ch 16
                                             # 0b10000000000000000000.to_bytes(4, "little"),  # ch 19
                                             print_msg=True)

    # For tinkering, set ZCD_NV_STARTUP_OPTION to cause clear network state on restart.
    # Otherwise the device will attempt to resume its network, which might be bollocks.
    if success:
        success = znp.zb_write_configuration(S,
                                             b'\x03',  # ZCD_NV_STARTUP_OPTION
                                             b'\x02',  # clear NV memory. Also possible to clear config settings.
                                             print_msg=True)

    # This modifies the ZCD_NV_POLL_RATE, which controls the interval with which the device contacts the coordinator with IEEE 802.15.4 "Data Request" packets
    # (see Wireshark sniffer). The compiled default is 1000ms. THe documentation wrongly states this is config_id 0x24 and has byte length. It is actually
    # config_id = 0x35 and is a 4 byte value (in ms) expressed in little-endian form
    if success:
        success = znp.zb_write_configuration(S,
                                             b'\x35',  # ZCD_NV_POLL_RATE
                                             b'\x98\x3A\x00\x00',  # 15,000ms, as PTVO, little-endian
                                             print_msg=True)

    if not success:
        raise Exception("FAILED to write device basic configuration")

    # ----------- issue a ZB_SYSTEM_RESET command to clear network state
    print("\nResetting to clear network state...", end="")
    znp.command_no_data(S, znp.ZB_SYSTEM_RESET)  # ignore response frame (but this DOES wait for it)
    print("Reset complete.\n")

    # ---------- Register the device and define the clusters. There is an alternative command in the Simple API. IDs here are big-endian
    znp.af_register(S,
                    endpoint=b'\x01',
                    app_prof_id=b'\x01\x04',  # Home Automation Profile (prescribed) = 260 decimal
                    app_device_id=b'\x00\x00',  # Device ID also in the HA Profile spec - this is the On/Off switch Id
                    app_dev_ver=b'\x01',  # I think the device version is not prescribed
                    in_cluster_ids=(  # this is a tuple
                        b'\x00\x00',  # Basic Cluster, which is device info such as name, manufacturer etc
                        b'\x00\x06'  # Make the switch also remote-controllable. This changes the switch state.
                    ),
                    out_cluster_ids=(  # this is a tuple
                        b'\x00\x06',  # On/Off switch is in output cluster list and so is a "client". This is the switch state, which is reported on change
                        #  b'\x00\x12',  # This is a "Multistate" (vs plain on/off id of 0x0006), for which reports would use attribute 0x0055
                    ),
                    print_msg=True
                    )
    # a second endpoint for the LED, since it also has a switch output cluster to report state
    znp.af_register(S,
                    endpoint=b'\x02',
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
                    ),
                    print_msg=True
                    )

    # ----------- join the network, waiting for the device state to become DEV_END_DEVICE
    # there are 2 options (and it is to be determined whether these can be mixed (simple/not) with the register api call used:
    # - ZB_START_REQUEST (0x2600) [Simple API], which takes no parameters and returns a plain (no data) ZB_START_REQUEST_RSP (0x6600) immediately, and
    # - ZDO_STARTUP_FROM_APP, which takes a parameter and returns a RSP with a network state byte
    input("MAKE SURE Zigbee2MQTT is accepting join requests and then hit any key. (Otherwise you get status=2 [searching for PAN] from ZDO_STATE_CHANGE_IND)")
    fr = znp.send_and_await_response(S,
                                     b'\x01' + znp.ZDO_STARTUP_FROM_APP + '\x00',  # ZDO_STARTUP_FROM_APP with 0 delay
                                     print_msg=True)  # this should cause a response 0x6540 with network state code + multiple ZDO_STATE_CHANGE_IND
    device_ready = False
    state_2_count = 0
    while not device_ready:
        if fr.command == znp.ZDO_STARTUP_FROM_APP_RSP:
            print("Startup response: network status code = {}".format(fr.data[0].to_bytes(length=1, byteorder="big")))
        elif fr.command == znp.ZDO_STATE_CHANGE_IND:  # see devStates_t enum in Z-Stack Home 1.2\Components\stack\zdo\ZDApp.h
            state = fr.data[0]
            if state == 2:  # DEV_NWK_DISC
                state_2_count += 1
                print(".", end="")
            else:
                print(f"State = {state}")  # normally steps through 3 DEV_NWK_JOINING -> 5 DEV_END_DEVICE_UNAUTH -> 6 DEV_END_DEVICE
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
# set up for reporting events at 10s intervals, LED change events at 7s intervals, and sw change events at 12s
last_time_report = int(time() // 10) % 2
last_time_led_event = int(time() // 7) % 2
last_time_sw_event = int(time() // 12) % 2
report_seq_no = 0
# device state variables
led_state_onoff = False  # LED off
sw_state_onoff = False  # plain switch off
while running:
    # check for incoming messages which correspond to the Z2M "interview"
    if S.inWaiting():
        print("--------------")
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
            if in_msg.zcl.frame_control == b'\x01':  # This is a command "local or specific to a cluster". e.g. a plain on/off command to cluster 6
                # also 0x01 indicates the default repsonse is not disabled. Ideally, this should be treated properly, but path of least action...
                print("Local/specific command to cluster", in_msg.cluster_id.hex())
                if in_msg.cluster_id == b'\x00\x06':
                    ep = in_msg.dst_endpoint[0]
                    # on/off commands are very simple. ZCL will have FCF=0x01 + the sequence + either 0x01 or 0x00 for "on" or "off"
                    set_state = bool(in_msg.zcl.zcl_command[0])
                    if ep == 1:
                        sw_state_onoff = set_state
                    elif ep == 2:
                        led_state_onoff = set_state
                    else:
                        print(print(f"Bad endpoint {ep}"))
                        continue

                    print(f"=> device on endpoint {ep} set to:  " + ("on" if set_state else "off"))
                    # response
                    data = znp.ZclFrameDefaultResponse(response_to=in_msg.zcl).zcl_message()
                else:
                    print("No support for that cluster")
                    continue

            elif in_msg.zcl.zcl_command == b'\x00':  # Read Attributes
                print("ZCL Command = read attributes (0x00)")
                cluster_provider = None
                if in_msg.cluster_id == b'\x00\x00':
                    cluster_provider = znp.BasicClusterAttributeParts(model_identifier="ZNP-Test")
                elif in_msg.cluster_id == b'\x00\x06':
                    # this cluster is on two endpoints ([0] gets int value of first and only byte), so:
                    if in_msg.dst_endpoint[0] == 1:  # plain switch
                        cluster_provider = znp.OnOffReadAttributeParts(sw_state_onoff)
                    elif in_msg.dst_endpoint[0] == 2:  # LED state
                        cluster_provider = znp.OnOffReadAttributeParts(led_state_onoff)                    

                if cluster_provider is None:
                    print("Unsupported cluster {}; cannot respond".format(in_msg.cluster_id.hex()))
                    continue

                data = znp.ZclFrameReadAttributesResponse(response_to=in_msg.zcl, cluster_provider=cluster_provider).zcl_message()

            # AF_DATA_REQUEST 0x2401
            print("Sending response...")
            out_msg = (10 + len(data)).to_bytes(length=1, byteorder="big") + znp.AF_DATA_REQUEST + \
                in_msg.src_addr + in_msg.src_endpoint + in_msg.dst_endpoint + in_msg.cluster_id[::-1] + in_msg.transaction_seq_no + b'\x00' + b'\x10' +\
                len(data).to_bytes(length=1, byteorder="big") + data
            rsp_success = znp.send_and_check_success(S, out_msg, znp.AF_DATA_REQUEST_RSP, print_msg=True)
            print("AF_DATA_REQUEST_RSP success?", rsp_success)  # ALSO likely to be AF_DATA_CONFIRM for request, in addition to AF_DATA_REQUEST response
        else:
            print("Unexpected message received:", f)

    # output event or make report
    time_report = int(time() // 10) % 2
    if time_report != last_time_report:
        last_time_report = time_report
        print("--------------")
        print("Sending periodic report (AF_DATA_REQUEST) for endpoint 2 (LED)")
        # Only cluster 0x0006 but also note that these reports include LQI (as part of the metadata), which shows up in Z2M. No reports = no LQI!
        cluster_provider = znp.OnOffReadAttributeParts(led_state_onoff, for_report=True)
        znp.send_report(S, 2, cluster_provider, report_seq_no, print_msg=True)  # endpoint 2
        report_seq_no += 1

    time_led_event = int(time() // 7) % 2
    if time_led_event != last_time_led_event:
        last_time_led_event = time_led_event
        led_state_onoff = not led_state_onoff
        print("-----\n\tLED changed to:  " + ("on" if led_state_onoff else "off"))

    time_sw_event = int(time() // 12) % 2
    if time_sw_event != last_time_sw_event:
        last_time_sw_event = time_sw_event
        sw_state_onoff = not sw_state_onoff
        print("-----\n\tSw changed to:  " + ("on" if sw_state_onoff else "off"))
        # the switch reports each state change (on and off) when they happen. (see above, there is no periodic report for endpoint 1)
        cluster_provider = znp.OnOffReadAttributeParts(sw_state_onoff, for_report=True)
        znp.send_report(S, 1, cluster_provider, report_seq_no, print_msg=True)  # endpoint 1
        report_seq_no += 1

S.close()
