import sys
from twisted.logger import Logger
from twisted.logger import LogLevel, ILogObserver, FilteringLogObserver, LogLevelFilterPredicate, textFileLogObserver
from twisted.internet.endpoints import clientFromString
from twisted.application.internet import ClientService, backoffPolicy
from twisted.internet import reactor
from twisted.application import service
from twisted.internet.protocol import Protocol
from twisted.internet.serialport import SerialPort
from serial.serialutil import SerialException

from mqtt.client.factory import MQTTFactory

log = Logger(namespace='mqtt-tristar')
loglevel = LogLevel.info
filterlog = True

MQTT_EP = clientFromString(reactor, "tcp:localhost:1883")
MQTT_TOPIC = {
    'hue': 'cbus/status/HOME/254/56/80/level',
    'saturation': 'cbus/status/HOME/254/56/81/level',
    'value': 'cbus/status/HOME/254/56/82/level',
    }

HUES = []
for i in range(101):
	HUES.append('\xaa\x7f\x3d' + chr(100-i) + chr(i) + chr(0) + '\x20')
for i in range(1, 101):
	HUES.append('\xaa\x7f\x3d' + chr(0) + chr(100-i) + chr(i) + '\x20')
for i in range(1, 100):
	HUES.append('\xaa\x7f\x3d' + chr(i) + chr(0) + chr(100-i) + '\x20')
OFF = '\xaa\x7f\x15\x14'
ON = '\xaa\x7f\x1c\x1b'

def _noop():
    pass

class TristarProtocol(Protocol):
    def __init__(self):
        self._serial = None
        self._received = ''
        self._sent = ''
        self._buffer = ''
        self._failedAttempts = 0
        self._retryPolicy = backoffPolicy(2)
        self.connect()
        self._timedout = False
        self._timeoutCancel = _noop

    def retry(self):
        self._failedAttempts += 1
        delay = self._retryPolicy(self._failedAttempts)
        log.info("Scheduling retry serial connection {attempt} in {seconds} seconds.",
            attempt=self._failedAttempts, seconds=delay)
        reactor.callLater(delay, self.connect)

    def connect(self):
        try:
            self._serial = SerialPort(self, '/dev/tristar', reactor, 57600)
            self._failedAttempts = 0
        except SerialException as e:
            log.debug("Can't connect serial : {e}", e=str(e))
            self.retry()
        except:
            raise

    def dataReceived(self, data):
        if self._sent and not self._timedout:
            self._received = self._received + data
            if len(self._received) >= len(self._sent):
                if self._sent in self._received:
                    log.debug('Success {sent} (received {received})',
                        sent=self._sent.encode('hex'), received=self._received.encode('hex'))
                    self._sent = ''
                    self._received = ''
                    self._timeoutCancel()
                    self._timeoutCancel = _noop
                else:
                    log.debug('Resending {sent} (received {received})',
                        sent=self._sent.encode('hex'), received=self._received.encode('hex'))
                    self.write(self._sent)

    def _timeout(self):
        self._timedout = True
        self._timeoutCancel = _noop
        self._serial.flushOutput()
        self._serial.flushInput()
        self._sent = ''
        self._received = ''
        self._buffer = ''

    def write(self, data):
        self._timedout = False
        self._timeoutCancel()
        self._timeoutCancel = _noop

        if self._serial:
            self._serial.flushOutput()
            self._serial.flushInput()
            self._received = ''
            self._serial.writeSomeData(data)
            self._sent = data
            self._buffer = ''
            self._timeoutCancel = reactor.callLater(5, self._timeout).cancel
        else:
            self._buffer = data

    def connectionMade(self):
        log.info("Serial connected")
        if self._buffer:
            self.write(self._buffer)

    def connectionLost(self, reason):
        self._serial = None
        self.retry()


class MQTTService(ClientService):
    def __init__(self, *args, **kwargs):
        self.protocol = None
        self.hue = HUES[0]
        self.saturation = 0
        self.value = 0
        self._serial = TristarProtocol()

        ClientService.__init__(self, *args, **kwargs)

    def startService(self):
        ClientService.startService(self)
        self.whenConnected().addCallback(self.connect)

    def stopService(self):
        self.protocol.disconnect()
        ClientService.stopService(self)

    def subscribe(self, *args):
        self.protocol.setWindowSize(10)
        self.protocol.subscribe(MQTT_TOPIC['hue'], 1 )
        self.protocol.subscribe(MQTT_TOPIC['saturation'], 1 )
        self.protocol.subscribe(MQTT_TOPIC['value'], 1 )
        self.protocol.setPublishHandler(self.onPublish)

    def connect(self, protocol):
        self.protocol = protocol
        d = self.protocol.connect("mqtt-tristar")
        d.addCallback(self.subscribe)

        def retryConnect():
            self.whenConnected().addCallback(self.connect)

        def delayRetryConnect(reason):
            self.protocol = None
            reactor.callLater(1, retryConnect)

        self.protocol.setDisconnectCallback(delayRetryConnect)

    def onPublish(self, topic, payload, qos, dup, retain, msgId):
        if topic == MQTT_TOPIC['value']:
            if payload == '0':
                self.value = 0
                self.sendOff()
            else:
                self.value = 255
                self.sendOn()
        elif topic == MQTT_TOPIC['hue']:
            self.hue = HUES[int(round(int(payload)/255.0*299))]
            log.debug("Setting hue - {hue}", hue = self.hue.encode('hex'))
            if self.value: self.sendOn()

        log.debug("{topic} {payload}", topic=topic, payload=payload)

    def sendOn(self):
        log.debug("Sending on command - {hue} {on}", hue=self.hue.encode('hex'), on=ON.encode('hex'))
        self._serial.write(self.hue+ON)

    def sendOff(self):
        log.debug("Sending off command - {off}", off=OFF.encode('hex'))
        self._serial.write(OFF)



application = service.Application("mqtttristar")
service.IProcess(application).processName = "mqtttristar"
serviceCollection = service.IServiceCollection(application)

mqtt_service = MQTTService(MQTT_EP,
    MQTTFactory(profile=MQTTFactory.SUBSCRIBER))
mqtt_service.setServiceParent(serviceCollection)

if filterlog:
    isLevel = LogLevelFilterPredicate(loglevel)
    lo = FilteringLogObserver(observer=textFileLogObserver(sys.stdout), predicates=[isLevel])
    application.setComponent(ILogObserver, lo)
