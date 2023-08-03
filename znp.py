from serial import Serial


class BasicClusterAttributeParts:
    """
    Produces the byte sequences for a Read Attributes Response Command, including the attribute identifier, status, data type, and value
    """
    cluster_id = b'\x00\x00'
    supported_attributes = (b'\x00\x00', b'\x00\x04', b'\x00\x05', b'\x00\x07', b'\x40\x00')

    def __init__(self, model_identifier, manufacturer_name="ARC12", sw_build="test-build"):
        # TODO allow more customisation here.
        self.model_identifier = model_identifier
        self.manufacturer_name = manufacturer_name
        self.sw_build = sw_build

    def get_part(self, attribute_id):
        """

        :param attribute_id: big-endian attribute id
        :type attribute_id: bytes
        :return:
        """

        # we need the little-endian form for response messages, while working with big-endian for user-facing and Python API parameters
        attribute_id_le = attribute_id[::-1]

        # Default = use a status of 0x86, which means UNSUPPORTED_ATTRIBUTE and include no value
        part = attribute_id_le + b'\x86'

        # Commented out elifs are those which are requested by Z2M but for which I will return a "not supported" (for now)
        if attribute_id == b'\x00\x00':  # ZCL Version
            part = attribute_id_le + b'\x00' + b'\x30' + b'\x08'  # - what should this be for HA 1.2????? Used 8 for now as latest ZCL doc (which is Z3!)
        # elif attribute_id == b'\x00\x01':  # ApplicationVersion
        #     pass
        # elif attribute_id == b'\x00\x02':  # StackVersion
        #     pass
        # elif attribute_id == b'\x00\x03':  # HWVersion
        #     pass
        elif attribute_id == b'\x00\x04':
            part = attribute_id_le + b'\x00' + zcl_string(self.manufacturer_name)
        elif attribute_id == b'\x00\x05':
            part = attribute_id_le + b'\x00' + zcl_string(self.model_identifier)
        # elif attribute_id == b'\x00\x06':  # DateCode
        #     pass
        elif attribute_id == b'\x00\x07':  # PowerSource. This is mandatory!
            part = attribute_id_le + b'\x00' + b'\x30' + b'\x03'  # last byte 0x00 means "unknown", 0x03 means "battery"
        elif attribute_id == b'\x40\x00':  # SWBuildID is optional according to Zigbee spec but Z2M logs an error without (although it is not breaking)
            part = attribute_id_le + b'\x00' + zcl_string(self.sw_build)

        return part


class OnOffReadAttributeParts:
    """
    Produces the byte sequences for a Read Attributes Response Command, including the attribute identifier, status, data type, and value
    """
    cluster_id = b'\x00\x06'
    supported_attributes = (b'\x00\x00',  # on/off
                            )

    def __init__(self, on_off_state):
        """

        :param on_off_state: True of on
        :type on_off_state: boolean
        """
        self.on_off_state = on_off_state

    def get_part(self, attribute_id):
        """

        :param attribute_id: big-endian attribute id
        :type attribute_id: bytes
        :return:
        """

        # we need the little-endian form for response messages, while working with big-endian for user-facing and Python API parameters
        attribute_id_le = attribute_id[::-1]

        # Default = use a status of 0x86, which means UNSUPPORTED_ATTRIBUTE and include no value
        part = attribute_id_le + b'\x86'

        if attribute_id == b'\x00\x00':
            part = attribute_id_le + b'\x10' + self.on_off_state.to_bytes(1, "big")  # data type 0x10 is boolean

        return part


def zcl_string(s):
    """
    Generate the data-type + value representation of a string for ZCL frames
    :param s:
    :return:
    """
    return b'\x42' + len(s).to_bytes(length=1, byteorder="big") + s.encode(encoding="ascii")


class ZclFrameCommand:
    """
    Base class for ZCL messages received
    """
    def __init__(self, data):
        """
        :param data: "data" component from ZNP message
        :type data: bytes
        :
        """
        self.frame_control = data[0].to_bytes(length=1, byteorder="big")
        self.trans_seq_no = data[1].to_bytes(length=1, byteorder="big")  # NB as byte as this will be needed for replies
        self.zcl_command = data[2].to_bytes(length=1, byteorder="big")


class ZclFrameResponse:
    """
    Base class for a response to a ZNP message
    """
    def __init__(self, frame_control, trans_seq_no, zcl_command):
        """
        Params are all 1 byte length bytes objects
        :param frame_control:
        :type frame_control: bytes
        :param trans_seq_no:
        :param zcl_command:
        """
        self.frame_control = frame_control
        self.trans_seq_no = trans_seq_no
        self.zcl_command = zcl_command
        self.variables = []  # populate this in sub-class. this is the list of items for the message body

    def zcl_header(self):
        return self.frame_control + self.trans_seq_no + self.zcl_command

    def zcl_message(self):
        """
        returns header + body for ZCL response as a bytes object
        :return: bytes
        """
        return self.zcl_header() + b''.join(self.variables)


def zcl_fcf_flip(fcf):
    """
    FLips direction of a frame-control field
    :param fcf:
    :return:
    """
    return (fcf[0] ^ 0x08).to_bytes(1, "big")  # direction is bit 3


class ZclFrameReadAttributes(ZclFrameCommand):
    """
    Parses an AF-level data payload to ZCL components. TODO fix this to not just be for request frames.
    """
    def __init__(self, data):
        """

        :param data: "data" component of e.g. AF_INCOMING_MSG
        :type data: bytes
        """
        super(ZclFrameReadAttributes, self).__init__(data)

        self.attribute_ids = []  # converted to big-endian from little-endian in the raw data
        for i in range(3, len(data), 2):
            self.attribute_ids.append(data[i+1: i-1: -1])


class ZclFrameReadAttributesResponse(ZclFrameResponse):
    """
    Creates a response to read-attributes command
    """
    def __init__(self, response_to, cluster_provider):
        """

        :param response_to: ZCL command frame object (subclass of ZclFrameCommand)
        :type response_to: ZclFrameReadAttributes
        :param cluster_provider: object with get_part(attribute_id) method, which will generate the data parts for the response
        """
        super(ZclFrameReadAttributesResponse, self).__init__(zcl_fcf_flip(response_to.frame_control), response_to.trans_seq_no, b'\x01')

        self.variables = [cluster_provider.get_part(a) for a in response_to.attribute_ids]


class ZclFrameReport(ZclFrameResponse):
    """
    Creates report ZCL (very similar to response to read-attributes command)
    """
    def __init__(self, cluster_provider, sequence_no):
        """

        :param cluster_provider: object with get_part(attribute_id) method, which will generate the data parts for the response
        """
        super(ZclFrameReport, self).__init__(b'\x18', sequence_no.to_bytes(1, "big"), b'\x0a')

        self.variables = [cluster_provider.get_part(a) for a in cluster_provider.supported_attributes]


class ZclFrameDefaultResponse(ZclFrameResponse):
    """
    Creates a response to local command
    """
    def __init__(self, response_to):
        """

        :param response_to: ZCL command frame object (subclass of ZclFrameCommand)
        :type response_to: ZclFrameReadAttributes
        """
        super(ZclFrameDefaultResponse, self).__init__(b'\x18', response_to.trans_seq_no, b'\x0b')  # fixed FCF; 0x0b is "default response"

        self.variables = [response_to.zcl_command, b'\x00']  # the body of the default response ZCL is the command which was sent + a success flag


class AfIncomingMessage:
    """
    AF_INCOMING_MESSAGE is Simple API
    This will handle both attribute requests and "local/cluster-specific" commands (have no attributes), in spite of the use of ZclFrameReadAttributes
    for self.zcl (in the local case, this is an empty list for messages I've seen so far).
    """
    def __init__(self, f):
        """

        :param f:
        :type f: ZnpFrameBody
        """
        self.is_af_incoming_message = False

        if f.command != b'\x44\x81':
            return
        self.is_af_incoming_message = True

        # the command id says the frame is an AF_INCOMING_MESSAGE, so parse it. Unfortunately, there appears to be no documentation on the structure
        # other than what I can infer from how Z-tool shows the message. For example, I DONT KNOW WHETHER THE 16bit PARTS ARE BIG-ENDIAN or not
        self.group_id = f.data[0:2][::-1]  # this and next two come in as little-endian but Python API and UI convention is big-endian
        self.cluster_id = f.data[2:4][::-1]
        self.src_addr = f.data[4:6][::-1]
        self.src_endpoint = f.data[6].to_bytes(length=1, byteorder="big")
        self.dst_endpoint = f.data[7].to_bytes(length=1, byteorder="big")
        self.was_broadcast = f.data[8].to_bytes(length=1, byteorder="big")
        self.lqi = f.data[9]  # integer
        self.security_use = f.data[10].to_bytes(length=1, byteorder="big")
        self.timestamp = f.data[11:15]
        self.transaction_seq_no = f.data[15].to_bytes(length=1, byteorder="big")
        self.data_len = f.data[16]  # somewhat redundant as a public property
        self.zcl_raw = f.data[17:17+self.data_len]  # oddly, the raw frame data has 3 extra bytes after those indicated by data_len, which don't show in sniffer
        self.zcl = ZclFrameReadAttributes(self.zcl_raw)


class ZnpFrameBody:  # i.e. the ZNP frame as sent over UART but without the SOF byte and the FCS byte. Length becomes implicit in len(self.data)
    # TODO probably add a timeout so this isnt perpetually blocking in case of weirdness or bugs!
    def __init__(self, s):
        """

        :param s:
        :type s: Serial
        """

        self.command = None  # 2 bytes.
        self.data = None
        self.fcs_ok = False

        # wait until the SOF, read the length and then await the complete message (as defined by length)
        sof_found = False
        while not sof_found:
            b = s.read(1)
            if b == b'\xfe':
                sof_found = True
        b = s.read(1)  # data length
        data_length = b[0]  # data length

        self.command = s.read(2)

        self.data = s.read(data_length)

        # and the CRC as an integer
        fcs = s.read(1)[0]

        # check CRC - use all body = length + command + data
        xor8 = data_length ^ self.command[0] ^ self.command[1]
        for b in self.data:
            xor8 = xor8 ^ b

        self.fcs_ok = xor8 == fcs
        if not self.fcs_ok:
            print(f"BAD! Computed XOR8 = {xor8}. Compare to frame FCS = {fcs}")

    def __str__(self):
        return "Cmd: " + self.command.hex() + " Body: " + self.data.hex(sep=' ')


# ZNP message command ids
SYS_RESET_IND = b'\x41\x80'
ZB_WRITE_CONFIGURATION_RSP = b'\x66\x05'
ZB_SYSTEM_RESET = b'\x46\x09'


def calc_append_fcs(msg):  # note that bytes objects are immutable
    """
    Append XOR8 checksum to message
    :param msg: message, EXcluding the SOF
    :type msg: bytes
    :return: full framed message from SOF to FCS inclusive
    """
    xor8 = 0
    for b in msg:
        xor8 = xor8 ^ b
    return msg + xor8.to_bytes(length=1, byteorder="big")


def send_and_await_response(s, msg, prepend_sof=True, append_fcs=True, print_msg=False):
    """

    :param s:
    :param msg:
    :param prepend_sof: whether to add 0xfe before the passed message = the Start of Frame byte
    :param append_fcs: whetner to compute the XOR8 checksum and append to passed message before sending
    :param print_msg:
    :return:
    """

    framed_message = (b'\xfe' if prepend_sof else b'') + (calc_append_fcs(msg) if append_fcs else msg)

    if print_msg:
        print("TX:", framed_message.hex(sep=" "))
    s.write(framed_message)

    f = ZnpFrameBody(s)
    if print_msg:
        print("[Response] RX body:", f)

    return f


def send_and_check_success(s, msg, response_command_id, prepend_sof=True, append_fcs=True,  print_msg=False):
    """

    :param s:
    :param msg:
    :param response_command_id: 2 byte command id of response message expected for sent msg:
    :param prepend_sof: whether to add 0xfe before the passed message = the Start of Frame byte
    :param append_fcs: whetner to compute the XOR8 checksum and append to passed message before sending
    :param print_msg:
    :return:
    """
    # wait for response and return boolean
    f = send_and_await_response(s, msg, prepend_sof, append_fcs,  print_msg)
    return (f.command == response_command_id) and (f.data == b'\x00')  # 1 data byte = 0x00 for "success"


def zb_write_configuration(s, config_id, value, print_msg=False):
    """

    :param s: instance of PySerial
    :type s: Serial
    :param config_id:
    :type config_id: bytes
    :param value:
    :type value: bytes
    :param print_msg:
    :type print_msg: bool
    :return:
    """
    data_len = 2 + len(value)  # config-id + value-len + value
    msg = data_len.to_bytes(length=1, byteorder='big') + b'\x26\x05' + config_id + len(value).to_bytes(length=1, byteorder="big") + value

    return send_and_check_success(s,
                                  msg,
                                  b'\x66\x05',  # ZB_WRITE_CONFIGURATION_RSP
                                  prepend_sof=True,
                                  append_fcs=True,
                                  print_msg=print_msg)


def af_register(s,
                endpoint,  # A single endpoint is being used, 1
                app_prof_id,  # (prescribed)
                app_device_id,  # Device ID also in the HA Profile spec
                app_dev_ver,  #
                in_cluster_ids=tuple(),
                out_cluster_ids=tuple(),
                latency_req=b'\x00',
                print_msg=False
                ):
    """
    NB: the two-byte parameters are passed in big-endian form (and are converted internally to this function). Includes cluster ids
    :param s:
    :param endpoint: single byte for endpoint no/id
    :param app_prof_id: 2 byte profile id - generally  Home Automation Profile, a prescribed id from Zigbee specs
    :param app_device_id: 2 byte device id - generally a prescribed id from the profile
    :param app_dev_ver: I think the device version is not prescribed
    :param latency_req: see ZNP docs. I have not looked into whether values other than the default are useful
    :param in_cluster_ids: list/tuple of cluster ids, each being a 2 byte id. may be empty
    :param out_cluster_ids:
    :param print_msg:
    :return:
    """
    # little-endian needed for msg
    le_in_cluster_ids = [cid[::-1] for cid in in_cluster_ids]
    le_out_cluster_ids = [cid[::-1] for cid in out_cluster_ids]
    data_len = 9 + 2 * len(in_cluster_ids) + 2 * len(out_cluster_ids)
    msg = data_len.to_bytes(length=1, byteorder='big') + b'\x24\x00' + \
        endpoint + app_prof_id[::-1] + app_device_id[::-1] + app_dev_ver + latency_req + \
        len(in_cluster_ids).to_bytes(length=1, byteorder="big") + b''.join(le_in_cluster_ids) + \
        len(out_cluster_ids).to_bytes(length=1, byteorder="big") + b''.join(le_out_cluster_ids)

    return send_and_check_success(s,
                                  msg,
                                  b'\x64\x00',
                                  prepend_sof=True,
                                  append_fcs=True,
                                  print_msg=print_msg)


def command_no_data(s, command_id):
    """

    :param s:
    :param command_id:
    :type command_id: bytes
    :return:
    """
    msg = b'\x00' + command_id
    s.write(b'\xfe' + calc_append_fcs(msg))  # add SOF and FCS

    # wait for response and return it
    return ZnpFrameBody(s)
