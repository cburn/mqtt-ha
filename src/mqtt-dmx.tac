import sys
from twisted.logger import Logger
from twisted.internet.endpoints import clientFromString
from twisted.application.internet import ClientService, backoffPolicy
from twisted.internet import reactor
from twisted.application import service
from twisted.internet.protocol import Protocol
from twisted.internet.serialport import SerialPort
from serial.serialutil import SerialException
from twisted.logger import LogLevel, ILogObserver, FilteringLogObserver, LogLevelFilterPredicate, textFileLogObserver

from colorsys import hsv_to_rgb

from mqtt.client.factory import MQTTFactory

log = Logger(namespace='mqtt-dmx')
loglevel = LogLevel.info
filterlog = True

MQTT_EP = clientFromString(reactor, "tcp:localhost:1883")

class DMXRGB(object):
    def __init__(self, name, hue, saturation, value, base_channel):
        self.name = name
        self.topics = {
            'hue': hue,
            'saturation': saturation,
            'value': value
        }
        self.channel = base_channel
        self.hue = 0
        self.saturation = 0
        self.value = 0

    def dmx(self):
        rgb = hsv_to_rgb(self.hue/255.0, self.saturation/255.0, self.value/255.0)
        ret = ''
        for i in range(3):
            ret = ret + chr(self.channel+i) + chr(int(rgb[i]*255.0))
        return ret

    def update(self, topic, payload, _write):
        if topic == self.topics['hue']:
            self.hue = int(payload)
        elif topic == self.topics['saturation']:
            self.saturation = int(payload)
        elif topic == self.topics['value']:
            self.value = int(payload)
        _write(self.dmx())



MQTT_DMX = [
    DMXRGB(
        'kitchen',
        'cbus/status/HOME/254/56/83/level',
        'cbus/status/HOME/254/56/84/level',
        'cbus/status/HOME/254/56/85/level',
        1
    ),
    DMXRGB(
        'frontroom',
        'cbus/status/HOME/254/56/86/level',
        'cbus/status/HOME/254/56/87/level',
        'cbus/status/HOME/254/56/88/level',
        4
    )
]


class DMXProtocol(Protocol):
    def __init__(self):
        self._serial = None
        self._received = ""
        self._buffer = ""
        self._failedAttempts = 0
        self._retryPolicy = backoffPolicy()
        self.connect()

    def retry(self):
        self._failedAttempts += 1
        delay = self._retryPolicy(self._failedAttempts)
        log.info("Scheduling retry serial connection {attempt} in {seconds} seconds.",
            attempt=self._failedAttempts, seconds=delay)
        reactor.callLater(delay, self.connect)

    def connect(self):
        try:
            self._serial = SerialPort(self, '/dev/dmx', reactor)
        except SerialException:
            self.retry()
        except:
            raise

    def dataReceived(self, data):
            self._received = self._received + data.encode('hex')

    def write(self, data):
        if self._serial:
            self._serial.writeSomeData(data)
        else:
            self._buffer = self._buffer + data

    def connectionMade(self):
        log.info("Serial connected")
        if self._buffer:
            self.write(self._buffer)
            self._buffer = ""

    def connectionLost(self, reason):
        self._serial = None
        self.connect()


class MQTTService(ClientService):
    def __init__(self, *args, **kwargs):
        self.protocol = None
        self._serial = DMXProtocol()

        ClientService.__init__(self, *args, **kwargs)

    def startService(self):
        ClientService.startService(self)
        self.whenConnected().addCallback(self.connect)

    def stopService(self):
        self.protocol.disconnect()
        ClientService.stopService(self)

    def subscribe(self, *args):
        self.protocol.setWindowSize(3*len(MQTT_DMX))
        for d in MQTT_DMX:
            self.protocol.subscribe( d.topics['hue'], 0 )
            self.protocol.subscribe( d.topics['saturation'], 0 )
            self.protocol.subscribe( d.topics['value'], 0 )
        self.protocol.setPublishHandler(self.onPublish)

    def connect(self, protocol):
        self.protocol = protocol
        d = self.protocol.connect("mqtt-dmx")
        d.addCallback(self.subscribe)

        def retryConnect():
            self.whenConnected().addCallback(self.connect)

        def delayRetryConnect(reason):
            self.protocol = None
            reactor.callLater(1, retryConnect)

        self.protocol.setDisconnectCallback(delayRetryConnect)

    def onPublish(self, topic, payload, qos, dup, retain, msgId):
        for d in MQTT_DMX:
            if topic in d.topics.values():
                d.update(topic, payload, self._serial.write)
                break

        log.debug("{topic} {payload}", topic=topic, payload=payload)



application = service.Application("mqtt-dmx")
service.IProcess(application).processName = "mqtt-dmx"
serviceCollection = service.IServiceCollection(application)

mqtt_service = MQTTService(MQTT_EP,
    MQTTFactory(profile=MQTTFactory.SUBSCRIBER))
mqtt_service.setServiceParent(serviceCollection)

if filterlog:
    isLevel = LogLevelFilterPredicate(loglevel)
    lo = FilteringLogObserver(observer=textFileLogObserver(sys.stdout), predicates=[isLevel])
    application.setComponent(ILogObserver, lo)
