# -*- coding: utf-8 -*-

# Octowire Framework
# Copyright (c) ImmunIT - Jordan Ovrè / Paul Duncan
# License: Apache 2.0
# Paul Duncan / Eresse <pduncan@immunit.ch>
# Jordan Ovrè / Ghecko <jovre@immunit.ch>

import struct
import time

from hexformat.intelhex import IntelHex
from io import BytesIO
from tqdm import tqdm

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
            'description': 'Read the flash memory of AVR microcontrollers',
            'author': 'Jordan Ovrè / Ghecko <jovre@immunit.ch>, Paul Duncan / Eresse <pduncan@immunit.ch>'
        })
        self.options = {
            "spi_bus": {"Value": "", "Required": True, "Type": "int",
                        "Description": "SPI bus (0=SPI0 or 1=SPI1)", "Default": 0},
            "reset_line": {"Value": "", "Required": True, "Type": "int",
                           "Description": "GPIO used as the Reset line", "Default": 0},
            "spi_baudrate": {"Value": "", "Required": True, "Type": "int",
                             "Description": "SPI frequency (1000000 = 1MHz) maximum = 50MHz", "Default": 1000000},
            "dumpfile": {"Value": "", "Required": True, "Type": "file_w",
                         "Description": "Dump output filename", "Default": ""},
            "intelhex": {"Value": "", "Required": True, "Type": "bool",
                         "Description": "If True, dump the firmware in intelhex format;\n"
                                        "If False, save the dump in 'raw binary' format.",
                         "Default": True},
        }
        self.advanced_options.update({
            "detect_target": {"Value": "", "Required": True, "Type": "bool",
                              "Description": "Detect the target chip and automatically set the size option",
                              "Default": True}
        })
        self.advanced_options.update({
            "flash_size": {"Value": "", "Required": False, "Type": "hex",
                           "Description": "Flash size (bytes)", "Default": "0x00"}
        })
        self.dependencies.append("owfmodules.avrisp.device_id>=1.0.0")

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
        load_extended_addr = b'\x4d\x00'
        enable_mem_access_cmd = b'\xac\x53\x00\x00'
        dump = BytesIO()
        extended_addr = None
        self.logger.handle("Enabling Memory Access...", self.logger.INFO)

        # Drive reset low
        reset.status = 0

        # Enable Memory Access
        spi_interface.transmit(enable_mem_access_cmd)
        time.sleep(0.5)

        # Read flash loop
        for read_addr in tqdm(range(0, flash_size // 2), desc="Reading", unit='B', unit_scale=True,
                              unit_divisor=1024, ascii=" #",
                              bar_format="{desc} : {percentage:3.0f}%[{bar}] {n_fmt}/{total_fmt} Words "
                                         "[elapsed: {elapsed} left: {remaining}]"):
            # Load extended address
            if read_addr >> 16 != extended_addr:
                extended_addr = read_addr >> 16
                spi_interface.transmit(load_extended_addr + bytes([extended_addr]) + b'\x00')
            # Read low byte
            spi_interface.transmit(low_byte_read + struct.pack(">H", read_addr & 0xFFFF))
            dump.write(spi_interface.receive(1))
            # Read high byte
            spi_interface.transmit(high_byte_read + struct.pack(">H", read_addr & 0xFFFF))
            dump.write(spi_interface.receive(1))

        # Drive reset high
        reset.status = 1
        self.logger.handle("Successfully dumped {} bytes from flash memory.".format(flash_size), self.logger.SUCCESS)

        # Save the dump locally
        # Intel Hex file format
        if self.options["intelhex"]["Value"]:
            dump_hex = IntelHex(bytesperline=32)
            # Change the stream position to the address 0 of the BytesIO handler
            dump.seek(0)
            dump_hex = dump_hex.loadbinfh(dump)
            dump_hex.tofile(self.options["dumpfile"]["Value"])
        # Raw binary file format
        else:
            with open(self.options["dumpfile"]["Value"], 'wb') as f:
                f.write(dump.getvalue())
        dump.close()
        self.logger.handle("Dump saved into {}".format(self.options["dumpfile"]["Value"]), self.logger.RESULT)

    def process(self):
        spi_bus = self.options["spi_bus"]["Value"]
        reset_line = self.options["reset_line"]["Value"]
        spi_baudrate = self.options["spi_baudrate"]["Value"]

        if self.advanced_options["detect_target"]["Value"]:
            device = self.get_device_id(spi_bus, reset_line, spi_baudrate)
            if device is not None:
                self.advanced_options["flash_size"]["Value"] = int(device["flash_size"], 16)
            else:
                return

        spi_interface = SPI(serial_instance=self.owf_serial, bus_id=spi_bus)
        reset = GPIO(serial_instance=self.owf_serial, gpio_pin=reset_line)

        reset.direction = GPIO.OUTPUT

        # Reset is active-low
        reset.status = 1

        # Configure SPI with default phase and polarity
        spi_interface.configure(baudrate=spi_baudrate)

        # Check if detect is true and flash size > 0 or detect is false and flash size > 0
        if self.advanced_options["flash_size"]["Value"] > 0:
            self.logger.handle("Starting dump of the flash memory...", self.logger.INFO)
            self.dump(spi_interface, reset, self.advanced_options["flash_size"]["Value"])
        else:
            self.logger.handle("Invalid flash size", self.logger.ERROR)

    def run(self):
        """
        Main function.
        Dump the flash memory of an AVR device.
        :return: Nothing.
        """
        # If detect_octowire is True then detect and connect to the Octowire hardware. Else, connect to the Octowire
        # using the parameters that were configured. This sets the self.owf_serial variable if the hardware is found.
        self.connect()
        if not self.owf_serial:
            return
        try:
            self.process()
        except ValueError as err:
            self.logger.handle(err, self.logger.ERROR)
        except Exception as err:
            self.logger.handle("{}: {}".format(type(err).__name__, err), self.logger.ERROR)
