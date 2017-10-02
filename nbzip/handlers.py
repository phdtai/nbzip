from tornado import gen, web, locks
from notebook.utils import url_path_join
from notebook.base.handlers import IPythonHandler
from queue import Queue, Empty

import traceback
import urllib.parse
import threading
import json
import os
import jinja2
import zipfile

TEMP_ZIP_NAME = 'notebook.zip'

class ZipHandler(IPythonHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _emit_progress(self, progress):
        if isinstance(progress, Exception):
            self.emit({
                'phase': 'error',
                'message': str(progress),
                'output': '\n'.join([
                    l.strip()
                    for l in traceback.format_exception(
                        type(progress), progress, progress.__traceback__
                    )
                ])
            })
        else:
            self.emit({'output': progress, 'phase': 'zipping'})

    @gen.coroutine
    def emit(self, data):
        if type(data) is not str:
            serialized_data = json.dumps(data)
            if 'output' in data:
                self.log.info(data['output'].rstrip())
        else:
            serialized_data = data
            self.log.info(data)
        self.write('data: {}\n\n'.format(serialized_data))
        yield self.flush()

    @gen.coroutine
    def get(self):
        try:
            base_url = self.get_argument('baseUrl')

            # We gonna send out event streams!
            self.set_header('content-type', 'text/event-stream')
            self.set_header('cache-control', 'no-cache')

            self.emit({'output': 'Removing old {}...\n'.format(TEMP_ZIP_NAME), 'phase': 'zipping'})

            if os.path.isfile(TEMP_ZIP_NAME):
                os.remove(TEMP_ZIP_NAME)
                self.emit({'output': 'Removed old {}!\n'.format(TEMP_ZIP_NAME), 'phase': 'zipping'})
            else:
                self.emit({'output': '{} does not exist!\n'.format(TEMP_ZIP_NAME), 'phase': 'zipping'})

            self.emit({'output': 'Zipping files:\n', 'phase': 'zipping'})

            q = Queue()
            def zip():
                try:
                    file_name = None
                    zipf = zipfile.ZipFile(TEMP_ZIP_NAME, 'w', zipfile.ZIP_DEFLATED)
                    for root, dirs, files in os.walk('./'):
                        for file in files:
                            file_name = os.path.join(root, file)
                            q.put_nowait("{}\n".format(file_name))
                            zipf.write(file_name)
                    zipf.close()

                    # Sentinel when we're done
                    q.put_nowait(None)
                except Exception as e:
                    q.put_nowait(e)
                    raise e

            self.gp_thread = threading.Thread(target=zip)
            self.gp_thread.start()

            while True:
                try:
                    progress = q.get_nowait()
                except Empty:
                    yield gen.sleep(0.5)
                    continue
                if progress is None:
                    break
                self._emit_progress(progress)

            self.emit({'phase': 'finished', 'redirect': url_path_join(base_url, 'tree')})
        except Exception as e:
            self._emit_progress(e)


class UIHandler(IPythonHandler):
    def initialize(self):
        super().initialize()
        # FIXME: Is this really the best way to use jinja2 here?
        # I can't seem to get the jinja2 env in the base handler to
        # actually load templates from arbitrary paths ugh.
        jinja2_env = self.settings['jinja2_env']
        jinja2_env.loader = jinja2.ChoiceLoader([
            jinja2_env.loader,
            jinja2.FileSystemLoader(
                os.path.join(os.path.dirname(__file__), 'templates')
            )
        ])

    @gen.coroutine
    def get(self):
        self.write(
            self.render_template(
                'status.html'
            ))
        self.flush()