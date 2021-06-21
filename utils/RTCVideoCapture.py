from asyncio import transports
from os import set_blocking
from aiortc import (RTCSessionDescription, RTCPeerConnection, RTCConfiguration,
    RTCIceServer)
import aiortc
from aiortc.contrib.media import MediaRecorder
from aiortc.mediastreams import MediaStreamError
import socketio
import asyncio
import sys
import cv2
from contextlib import suppress

# Setting max fps
FPS = 60

#log_file = open("log.txt",'w')
log_file = sys.stderr

def log(*args, **kwargs):
    print(*args, **kwargs, file=log_file,flush=True)


class RTCVideoCapture:
    """Video Capture from a WebRTC Client

    this class creates the RTCPeerConnection
    and starts capturing the incoming track.

    ### Constructor ### 

    RTCVideoCapture()
        mostly creates the socket-io client used for signaling

    ### Methods ###

    start(signaling_url, signaling_port)
        This part was meant to be in the constructor but it is an async function
        It initiates the WebRTC Signaling process
    
    stop()
        Cleans a bit. Disconnect the socket, closes the PeerConnection
        and stops the frame polling loop
    
    read()
        return a couple. Second value is the last frame from the incoming
        MediaStreamTrack. First values is True when retrieving a frame was
        successful and False otherwise
    
    on(event)
        Simple decorator to add an event listener. Supported events are :
        'frame' -> triggers on each frames with parameter (frame)

    ### Hidden (or "private") Methods ###

    __start_waiting_frames()
        Loops indefinitely and retrieves frames from MediaStreamTrack at FPS.
        returns a co-routine. Calling cancel and awaiting this co-routine 
        stops polling frames.
        MediaStreamError exception is just forwarded
    
    __on_offer(data)
        All signaling logic lies in this function. It will :
        - Create a RTCPeerConnection
        - Attach some callback functions on it (see RTCPeerConnection callbacks)
        - Set the remote description from the data received in the offer
        - Set its LocalDescription and send it as an answer
    
    __call(type, *args, **kwargs)
        trigger the event. Only used to start a new 'frame' event

    ### RTCPeerConnection callbacks ###
        
    on('track')
        when a video track is received, store it

    on('connectionstatechange')
        If the state of the connection is 'failed' close it 
        If it is 'connected', start polling frames
    
    Other callbacks are just debug info

    ### Socket-IO callbacks : ###

    on('data')
        handles the data from the socket :
        Start RTCVideoCapture.__on_offer(data) when it receives an offer
    """

    PC_CONFIG = RTCConfiguration(
        iceServers = [
            RTCIceServer('stun:stun.l.google.com:19302')
        ]
    )

    # Constructor
    def __init__(self):
        self.track = None
        self.last_frame = None
        self.sio = socketio.AsyncClient(logger=True)

        # Setting callback for "data" event
        @self.sio.on('data')
        async def on_data(data):
            if data['type'] == 'offer':
                await self.__on_offer(data)
            else:
                log('case {} not supported'.format(data['type']))
        
        # Maybe add a callback for sio.on('disconnect')
        # and just raise an exception

        # Event handlers (mostly for the custom on frame)
        self.handlers = {}


    # Run after constructor
    async def start(self, signaling_url, signaling_port):
        await self.sio.connect(signaling_url + ':' + signaling_port)
        await self.sio.emit('consumer ready')
    
    async def stop(self):
        await self.sio.disconnect()
        await self.pc.close()

        self.task.cancel()
        await self.task

    async def read(self):
        if self.last_frame is not None:
            return True, self.last_frame
        else:
            return False, None

    async def __start_waiting_frames(self):
        try:
            while True:
                if self.track is not None:
                    frame = await self.track.recv()
                    if frame is not None:
                        # for the read() function
                        self.last_frame = frame
                        # for the on('frame') event
                        self.__call('frame',frame)
                # Don't faster than FPS
                await asyncio.sleep(1/FPS)
        except MediaStreamError:
            log('MediaStreamError occurred, stopping frames polling')
            raise
        except asyncio.CancelledError:
            log('Stopping frames polling')


    # Things to do when we receive an offer
    async def __on_offer(self, data):
        offer = RTCSessionDescription(sdp=data['sdp'], type=data['type'])
        self.pc = RTCPeerConnection(RTCVideoCapture.PC_CONFIG)

        # Setting callback for "track" event
        @self.pc.on("track")
        async def on_track(track):
            log('New Track from emitter')
            if track.kind == 'video':
                self.track = track

            @self.track.on("ended")
            async def on_ended():
                log('Track {} ended'.format(self.track.kind))
            

        @self.pc.on("connectionstatechange")
        async def on_state_change():
            log('State of the connection : {}'.format(self.pc.connectionState))
            if self.pc.connectionState == 'failed':
                await self.stop()
            elif self.pc.connectionState == 'connected':
                self.task = asyncio.create_task(self.__start_waiting_frames())

        @self.pc.on("iceconnectionstatechange")
        async def on_icestate_change():
            log('State of the iceconnection : {}'.format(self.pc.iceConnectionState))

        @self.pc.on("icegatheringstatechange")
        async def on_icegatheringstatechange():
            log(f"changed icegatheringstatechange {self.pc.iceGatheringState}")

        await self.pc.setRemoteDescription(offer)

        answer = await self.pc.createAnswer()
        await self.pc.setLocalDescription(answer)

        dict = {
            "sdp": answer.sdp,
            "type": answer.type
        }
        await self.sio.emit('data', dict)

    # Event handler :
    def on(self, event):
        log('registering an handler : {}'.format(event))
        def register_handler(handler):
            self.handlers[event] = handler
            return handler
        return register_handler
    
    def __call(self, type, *args, **kwargs):
        if type in self.handlers:
            self.handlers[type](*args, **kwargs)


"""
Example Usage
async def main():
    cap = RTCVideoCapture()

    # This works but if you want to retrieve one frame at a time, use cap.read()
    # @cap.on('frame')
    # def on_frame(frame):
        # log('On Frame')
        # img = frame.to_ndarray(format='bgr24')
        # log('printing image : {}'.format(img))
        # cv2.imshow('Video', img)

        # cv2.waitKey(1)

    await cap.start('http://localhost','9000')

    try:
        while True:
            log('frame')
            ret, frame = await cap.read()
            if ret:
                img = frame.to_ndarray(format='bgr24')
                cv2.imshow('image', img)
                cv2.waitKey(1)
            await asyncio.sleep(1/FPS)
    except:
        cv2.destroyAllWindows()
        await cap.stop()
        loop.stop()


loop = asyncio.get_event_loop()

try:
    loop.create_task(main())
    loop.run_forever()
finally:
    loop.close()
    log('END')
"""