document.addEventListener('DOMContentLoaded', async () => {
  await customElements.whenDefined('webots-view');
  const webotsView = document.querySelector('webots-view');

  if (webotsView && typeof webotsView.connect === 'function') {
    const messageListener = (event) => {
      if (event.data?.target === 'simserver') {
        webotsView.sendMessage(event.data.content);
      }
    };

    function onConnect() {
      console.log('Connected from Webots viewer');
      // Extract the port from the wsServer URL string.
      const url = new URL(webotsView._view.wsServer);
      let port;

      const host = url.hostname;
      // Check if there's a port in the URL's `port` property
      if (url.port) {
        port = url.port;
      } else {
        // If not, we assume the port is at the end of the pathname (format `ws://platform.fftai.top/2001`)
        // Extract the last segment after the last '/'
        const pathSegments = url.pathname.split('/');
        port = pathSegments[pathSegments.length - 1]; // Gets the last segment which should be the port
      }

      console.info = ()=>{}
      console.log = ()=>{}
      console.warn = ()=>{}
      console.error = ()=>{}

      window.addEventListener('message', messageListener);

      window.parent.postMessage({
        simulation: 'ready',
        host,
        port,
      }, '*');
    }

    function onDisconnect() {
      console.log('Disconnected from Webots viewer');
      window.removeEventListener('message', messageListener);
    }

    webotsView.onready = onConnect;
    webotsView.ondisconnect = onDisconnect;

    const wsUrl =
      'http://platform.fftai.top/1999/session?url=https://github.com/tiwater/fftai-webots-simulations/blob/main/gr-1/worlds/SonnyV4.wbt'; // This URL can be dynamically adjusted if needed
    webotsView.connect(wsUrl, 'x3d');
    console.log('Connected to Webots viewer');
  } else {
    console.error('webots-view does not have a connect method');
  }
});
