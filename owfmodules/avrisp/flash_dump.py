import struct

from intelhex import IntelHex
from prompt_toolkit.shortcuts import ProgressBar
from prompt_toolkit.shortcuts.progress_bar import formatters

from octowire_framework.module.AModule import AModule
from octowire.gpio import GPIO
from octowire.spi import SPI
from owfmodules.avrisp.device_id import DeviceID


class FlashDump(AModule):
    def __init__(self, owf_config):
        super(FlashDump, self).__init__(owf_config)
        self.meta.update({
            'name': 'AVR dump flash memory',
            'version': '1.0.0',
            'description': 'Module to dump the flash memory of an AVR device using the ISP protocol.',
            'author': 'Jordan Ovrè <ghecko78@gmail.com> / Paul Duncan <eresse@dooba.io>'
        })
        self.options = {
            "spi_bus": {"Value": "", "Required": True, "Type": "int",
                        "Description": "The octowire SPI bus (0=SPI0 or 1=SPI1)", "Default": 0},
            "reset_line": {"Value": "", "Required": True, "Type": "int",
                           "Description": "The octowire GPIO used as the Reset line", "Default": 0},
            "spi_baudrate": {"Value": "", "Required": True, "Type": "int",
                             "Description": "set SPI baudrate (1000000 = 1MHz) maximum = 50MHz", "Default": 1000000},
            "dumpfile": {"Value": "", "Required": True, "Type": "file_w",
                         "Description": "The dump filename", "Default": ""},
            "intelhex": {"Value": "", "Required": True, "Type": "bool",
                         "Description": "If True, dump the firmware in intelhex format;\n"
                                        "If False, save the dump in 'raw binary' format.",
                         "Default": True},
        }
        self.advanced_options.update({
            "detect_target": {"Value": "", "Required": True, "Type": "bool",
                              "Description": "Detect the target chip and set the size option", "Default": True}
        })
        self.advanced_options.update({
            "flash_size": {"Value": "", "Required": False, "Type": "hex",
                           "Description": "Flash size (Bytes)", "Default": "0x00"}
        })
        self.dependencies.append("owfmodules.avrisp.device_id>=1.0.0")
        self.pb_formatters = [
            formatters.Label(suffix=": "),
            formatters.Text(" "),
            formatters.Percentage(),
            formatters.Bar(start="[", end="]", sym_a="#", sym_b="#", sym_c="."),
            formatters.Text(" "),
            formatters.Progress(),
            formatters.Text(" Words "),
            formatters.Text(" [elapsed: "),
            formatters.TimeElapsed(),
            formatters.Text(" left: "),
            formatters.TimeLeft(),
            formatters.Text("]  "),
        ]

    def get_device_id(self, spi_bus, reset_line, spi_baudrate):
        device_id_module = DeviceID(owf_config=self.config)
        # Set DeviceID module options
        device_id_module.options["spi_bus"]["Value"] = spi_bus
        device_id_module.options["reset_line"]["Value"] = reset_line
        device_id_module.options["spi_baudrate"]["Value"] = spi_baudrate
        device_id_module.owf_serial = self.owf_serial
        device_id = device_id_module.run(return_value=True)
        return device_id

    def dump(self, spi_interface, reset, flash_size):
        low_byte_read = b'\x20'
        high_byte_read = b'\x28'
        dump = bytearray()
        # Drive reset low
        reset.status = 0
        with ProgressBar(formatters=self.pb_formatters) as pb:
            for current_addr in pb(range(0, flash_size // 2), label="Read"):
                # Read high byte
                spi_interface.transmit(high_byte_read + struct.pack("<H", current_addr))
                dump.extend(spi_interface.receive(1))
                # Read low byte
                spi_interface.transmit(low_byte_read + struct.pack("<H", current_addr))
                dump.extend(spi_interface.receive(1))
        # Drive reset high
        reset.status = 1
        self.logger.handle("Successfully dump {} byte(s) from flash memory.".format(flash_size), self.logger.SUCCESS)
        # Save the dump locally
        # Intel Hex file format
        if self.options["intelhex"]["Value"]:
            dump_hex = IntelHex()
            dump_hex.puts(0x00, bytes(dump))
            dump_hex.write_hex_file(self.options["dumpfile"]["Value"])
        # Raw binary file format
        else:
            with open(self.options["dumpfile"]["Value"], 'wb') as f:
                f.write(dump)
        self.logger.handle("Dump saved into {}".format(self.options["dumpfile"]["Value"]), self.logger.RESULT)

    def process(self):
        spi_bus = self.options["spi_bus"]["Value"]
        reset_line = self.options["reset_line"]["Value"]
        spi_baudrate = self.options["spi_baudrate"]["Value"]

        enable_mem_access_cmd = b'\xac\x53\x00\x00'

        if self.advanced_options["detect_target"]["Value"]:
            device = self.get_device_id(spi_bus, reset_line, spi_baudrate)
            if device is not None:
                self.advanced_options["flash_size"]["Value"] = int(device["flash_size"], 16)

        # Check flash size
        if self.advanced_options["flash_size"]["Value"] > 131072:
            self.logger.handle("Invalid flash size. Maximum allowed flash size: 131072", self.logger.ERROR)

        spi_interface = SPI(serial_instance=self.owf_serial, bus_id=spi_bus)
        reset = GPIO(serial_instance=self.owf_serial, gpio_pin=reset_line)

        reset.direction = GPIO.OUTPUT

        # Active Reset is low
        reset.status = 1

        # Configure SPI with default phase and polarity
        spi_interface.configure(baudrate=spi_baudrate)

        # Avoid enabling memory access twice
        if not self.advanced_options["detect_target"]["Value"]:
            self.logger.handle("Enable Memory Access...", self.logger.INFO)
            # Drive reset low
            reset.status = 0
            # Enable Memory Access
            spi_interface.transmit(enable_mem_access_cmd)
            # Drive reset high
            reset.status = 0

        if (self.advanced_options["detect_target"]["Value"] and self.advanced_options["flash_size"]["Value"] != 0) or \
            (not self.advanced_options["detect_target"]["Value"] and self.advanced_options["flash_size"]["Value"] != 0):
            self.logger.handle("Start dumping the flash memory of the device...", self.logger.INFO)
            self.dump(spi_interface, reset, self.advanced_options["flash_size"]["Value"])
        elif not self.advanced_options["detect_target"]["Value"] and self.advanced_options["flash_size"]["Value"] == 0:
            self.logger.handle("Invalid flash size", self.logger.ERROR)

    def run(self):
        """
        Main function.
        Dump the flash memory of an AVR device.
        :return: Nothing or bytes, depending of the 'return_value' parameter.
        """
        # If detect_octowire is True then Detect and connect to the Octowire hardware. Else, connect to the Octowire
        # using the parameters that were configured. It sets the self.owf_serial variable if the hardware is found.
        self.connect()
        if not self.owf_serial:
            return
        try:
            self.process()
        except ValueError as err:
            self.logger.handle(err, self.logger.ERROR)
        except Exception as err:
            self.logger.handle("{}: {}".format(type(err).__name__, err), self.logger.ERROR)
