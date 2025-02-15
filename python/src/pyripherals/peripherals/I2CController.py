import numpy as np
import time


class I2CController:
    """Class for controllers on the FPGA using I2C protocol.

    Attributes
    ----------
    I2C_MAX_TIMEOUT_MS : int
        Maximum wait time until transmission timeout in milliseconds.
    i2c : dict
        Dictionary of I2C memory buffer and data start location.
    fpga : FPGA
        FPGA instance this controller uses to communicate.
    endpoints : dict
        Endpoints on the FPGA this controller uses to communicate.
    """

    I2C_MAX_TIMEOUT_MS = 50

    def __init__(self, fpga, addr_pins, endpoints, i2c={'m_pBuf': [], 'm_nDataStart': 7}):
        self.i2c = i2c
        self.fpga = fpga
        self.addr_pins = addr_pins
        self.endpoints = endpoints

    @classmethod
    def create_chips(cls, fpga, addr_pins, endpoints):
        """Instantiate a number of new I2C chips.

        The FPGA and endpoints will be the same for all instantiated chips.

        Parameters
        ----------
        fpga : FPGA
            The fpga instance for the chip to connect with.
        addr_pins : list
            The list of addr_pins assigned to the new chips.

        Returns
        -------
        list
            A list of the newly instantiated chips in the same order addr_pins was given in.
        """

        return [cls(fpga=fpga, addr_pins=addr, endpoints=endpoints) for addr in addr_pins]

    # STARTS - Defines the preamble bytes after which a start bit is
    #      transmitted. For example, if STARTS=0x04, a start bit is
    #      transmitted after the 3rd preamble byte.
    # STOPS - Defines the preamble bytes after which a stop bit is
    #      transmitted. For example, if STOPS=0x04, a stop bit is
    #      transmitted after the 3rd preamble byte.
    # LENGTH - Length of the preamble in bytes.
    #
    # Note: If there is a one in the same position for both STARTS and STOPS,
    #       the stop takes precedence.

    # The preamble is the device address, byte address, and (if a read) device address again:
    #   preamble[0] = 0xA0; // devAddr (write)
    #   preamble[1] = 0x00; // byteAddress (MSB)
    #   preamble[2] = 0x00; // byteAddress (LSB)
    #   preamble[3] = 0xA1; // devAddr (read)

    # from the HDL

    def i2c_configure(self, preamble_length, starts, stops, preamble):
        """Configure the buffer for the next transmission."""

        if preamble_length > 7:
            print('Preamble data is too long')
            # throw DataTooLongException();

        self.i2c['m_pBuf'] = [None]*(4+preamble_length)
        self.i2c['m_pBuf'][0] = preamble_length
        self.i2c['m_pBuf'][1] = starts
        self.i2c['m_pBuf'][2] = stops
        # Payload length will be provided later.
        self.i2c['m_pBuf'][3] = 0
        for i in range(preamble_length):
            self.i2c['m_pBuf'][4+i] = preamble[i]

        self.i2c['m_nDataStart'] = 4 + preamble_length

    def i2c_transmit(self, data, data_length):
        """Send data along the SCL and SDA lines."""

        self.i2c['m_pBuf'][3] = data_length
        for i in range(data_length):
            self.i2c['m_pBuf'].append(data[i])

        # Reset the memory pointer and transfer the buffer.
        self.fpga.xem.ActivateTriggerIn(
            self.endpoints['MEMSTART'].address, self.endpoints['MEMSTART'].bit_index_low)
        for i in range(data_length + self.i2c['m_nDataStart']):
            # print('(transmit) WireIn Value = {}'.format(self.i2c['m_pBuf'][i]))
            mask = 0xff << self.endpoints['IN'].bit_index_low
            value = self.i2c['m_pBuf'][i] << self.endpoints['IN'].bit_index_low
            self.fpga.xem.SetWireInValue(
                self.endpoints['IN'].address, value, mask)
            self.fpga.xem.UpdateWireIns()
            self.fpga.xem.ActivateTriggerIn(
                self.endpoints['MEMWRITE'].address, self.endpoints['MEMWRITE'].bit_index_low)

        # Start I2C transaction
        self.fpga.xem.ActivateTriggerIn(
            self.endpoints['START'].address, self.endpoints['START'].bit_index_low)

        # Wait for transaction to finish
        for i in range(int(I2CController.I2C_MAX_TIMEOUT_MS)):
            self.fpga.xem.UpdateTriggerOuts()
            # change to waiting for True
            if self.fpga.xem.IsTriggered(self.endpoints['DONE'].address, (1 << self.endpoints['DONE'].bit_index_low)):
                return True
            time.sleep(0.001)

        print('Timeout error in transmit')

    def i2c_receive(self, data_length, data_transfer='wire', reset_pipe=True, readout=True):
        """Take in data from the SCL and SDA lines.
            
            Parameters
            ----------
            data_length : int
                Number of bytes expected to receive.
            data_transfer : str
                The form of the data transfer. Either 'wire', 'pipe'. Defaults to 'wire'.
            reset_pipe : bool
                Whether data in the pipe is reset before receive
            readout : bool
                Whether pipe read empties the FIFO or buffers

            Returns
            -------
            data or buf, e : list or bytearray, int
                The data or a bytearray and error code, depending on whether data_transfer was 'wire' or 'pipe'.
            """

        if data_transfer.lower() == 'pipe' and reset_pipe:
            try:
                self.endpoints['FIFO_RESET']
                self.endpoints['PIPE_OUT']
            except KeyError as e:
                raise KeyError('i2c_receive requires the I2C endpoints FIFO_RESET and PIPE_OUT. One or both are missing.')

            self.fpga.xem.ActivateTriggerIn(self.endpoints['FIFO_RESET'].address, self.endpoints['FIFO_RESET'].bit_index_low)

        self.i2c['m_pBuf'][0] |= 0x80
        self.i2c['m_pBuf'][3] = data_length

        # Reset the memory pointer and transfer the buffer.
        self.fpga.xem.ActivateTriggerIn(
            self.endpoints['MEMSTART'].address, self.endpoints['MEMSTART'].bit_index_low)

        for i in range(self.i2c['m_nDataStart']):
            # print('WireIn Value = {}'.format(self.i2c['m_pBuf'][i]))
            mask = 0xff << self.endpoints['IN'].bit_index_low
            value = self.i2c['m_pBuf'][i] << self.endpoints['IN'].bit_index_low
            self.fpga.xem.SetWireInValue(
                self.endpoints['IN'].address, value, mask)
            self.fpga.xem.UpdateWireIns()
            self.fpga.xem.ActivateTriggerIn(
                self.endpoints['MEMWRITE'].address, self.endpoints['MEMWRITE'].bit_index_low)

        # Start I2C transaction
        self.fpga.xem.ActivateTriggerIn(
            self.endpoints['START'].address,
            self.endpoints['START'].bit_index_low)

        # Wait for transaction to finish
        for _ in range(int(I2CController.I2C_MAX_TIMEOUT_MS / 10)):
            self.fpga.xem.UpdateTriggerOuts()

            if self.fpga.xem.IsTriggered(self.endpoints['DONE'].address,
                                         (1 << self.endpoints['DONE'].bit_index_low)):
                if not readout:
                    return
                if data_transfer.lower() == 'wire':
                    # Read data: Reset the memory pointer
                    self.fpga.xem.ActivateTriggerIn(
                        self.endpoints['MEMSTART'].address, self.endpoints['MEMSTART'].bit_index_low)
                    data = [None]*data_length
                    for i in range(data_length):  # for each byte we have three API calls
                        self.fpga.xem.UpdateWireOuts()
                        data_tmp = self.fpga.xem.GetWireOutValue(
                            self.endpoints['OUT'].address)
                        mask = 0xff << self.endpoints['OUT'].bit_index_low
                        data[i] = (
                            data_tmp & mask) >> self.endpoints['OUT'].bit_index_low
                        self.fpga.xem.ActivateTriggerIn(
                            self.endpoints['MEMREAD'].address, self.endpoints['MEMREAD'].bit_index_low)
                    return data
                if data_transfer.lower() == 'pipe':
                    return self.fpga.read_pipe_out(self.endpoints['PIPE_OUT'].address, data_length)
            time.sleep(0.01)

        print('Timeout Exception in Rx')

    # def i2c_write8(self, devAddr, regAddr, data_length, data):

    #     preamble = [devAddr & 0xfe, regAddr]
    #     self.i2c_configure(2, 0x00, 0x00, preamble)
    #     return self.i2c_transmit(data, data_length)

    def i2c_write_long(self, devAddr, regAddr, data_length, data):
        """Send a write command with given data to regAddr on devAddr.

        regAddr must be given in a list."""
        if (regAddr == [None]) or (regAddr == None):
            # for chips without register addresses -- just a single register
            preamble = [devAddr & 0xfe]
        else:
            preamble = [devAddr & 0xfe] + regAddr  # + data
        # def i2c_configure(self, data_length, starts, stops, preamble)
        self.i2c_configure(len(preamble), 0x00, 1 << len(preamble), preamble)
        return self.i2c_transmit(data, data_length)

    # Sequence is
    # [START] DEV_ADDR(W) REG_ADDR [START] DEV_ADDR(R) VALUE
    def i2c_read_long(self, devAddr, regAddr, data_length, data_transfer='wire', reset_pipe=True, readout=True):
        """Read data_length bytes from regAddr on devAddr.

        Parameters
        ----------
        devAddr : int
            8 bit address (don't set the read bit (LSB) since this is done in this function)
        regAddr : int
            Written to device (this is a list and must be even if length 1)
        data_length : int
            Number of bytes expected to receive
        data_transfer : str
            The form of the data transfer. Either 'wire' and 'pipe'. Defaults to 'wire'.
        reset_pipe : bool
            Whether data in the pipe is reset before receive
        readout : bool
            Whether pipe read empties the FIFO or buffers
        Returns
        -------
        data or buf, e : list or bytearray, int
            The data or a bytearray and error code, depending on whether data_transfer was 'wire' or 'pipe'.
        """
        
        if (regAddr == None) or (regAddr == [None]):
            # for chips without register addresses -- just a single register
            preamble = [devAddr | 0x01]
            # self.i2c_configure(1, 0x01, 0x00, preamble)
            self.i2c_configure(1, 0x00, 0x00, preamble)  # no starts needed

        else:
            preamble = [devAddr & 0xfe] + regAddr + [devAddr | 0x01]
            # signature: i2c_configure(data_length, starts (a one for each byte that gets a start), stops, preamble):
            start_positions = 0x01 << len(regAddr)
            self.i2c_configure(len(preamble), start_positions, 0x00, preamble)
        if readout:
            data = self.i2c_receive(data_length, data_transfer, reset_pipe, readout)
            return data
        else:
            self.i2c_receive(data_length, data_transfer, reset_pipe, readout)

    def reset_device(self):
        """Reset the I2C controller using an OK TriggerIn."""

        return self.fpga.xem.ActivateTriggerIn(
            self.endpoints['RESET'].address,
            self.endpoints['RESET'].bit_index_low)
