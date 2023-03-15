"""QuerySource Template Parser.
Jinja2 Template Engine.
"""
from typing import (
    Optional,
    Union
)
from pathlib import Path
from jinja2 import (
    Environment,
    FileSystemLoader,
    TemplateError,
    TemplateNotFound
)
from aiohttp import web
from navconfig import config, BASE_DIR
from navconfig.logging import logging


__version__ = '0.0.1'
__author__ = "Jesus Lara <jesuslarag@gmail.com>"


jinja_config = {
    'enable_async': True,
    'extensions': [
        'jinja2.ext.i18n',
        'jinja2.ext.loopcontrols',
        'jinja2.ext.do'
    ]
}

class TemplateParser:


    def __init__(self, template_dir: Union[list[Path], str] = None, **kwargs) -> None:
        self.env: Optional[Environment] = None
        if 'config' in kwargs:
            self.config = {**jinja_config, **kwargs['config']}
        else:
            self.config = jinja_config

        template_debug = config.getboolean('TEMPLATE_DEBUG', fallback=False)
        if template_debug is True:
            self.config['extensions'].append(
                'jinja2.ext.debug'
            )
        self.tmpl_dir = BASE_DIR.joinpath("templates")
        if self.tmpl_dir.exists():
            self.directory = [self.tmpl_dir]
        if isinstance(template_dir, list):
            # iterate over:
            for d in template_dir:
                if d is not None:
                    if isinstance(d, str):
                        d = Path(d).resolve()
                    if not d.exists():
                        raise ValueError(
                            f"Missing Template Directory: {d}"
                        )
                    self.directory.append(d)
        logging.debug('QS: Start Templating System')

    def setup(self, app: web.Application):
        """setup.
        Configure Jinja2 Template Parser for QuerySource.
        """
        ## Added template system to App
        if isinstance(app, web.Application):
            self.app = app # register the app into the Extension
        else:
            raise TypeError(
                f"Invalid type for Application Setup: {app}:{type(app)}"
            )
        # register the extension into the app
        self.app['templating'] = self
        # create loader:
        self.loader = FileSystemLoader(
            searchpath=self.directory
        )
        try:
            # TODO: check the bug ,encoding='ANSI'
            self.env = Environment(
                loader=self.loader,
                **self.config
            )
            compiled_path = str(self.tmpl_dir.joinpath('.compiled'))
            self.env.compile_templates(
                target=compiled_path, zip='deflated'
            )
        except Exception as err:
            raise RuntimeError(
                f'QS: Error loading Template Environment: {err}'
            ) from err

    def get_template(self, filename: str):
        """
        Get a template from Template Environment using the Filename.
        """
        try:
            return self.env.get_template(str(filename))
        except TemplateNotFound as ex:
            raise FileNotFoundError(
                f"Template cannot be found: {filename}"
            ) from ex
        except Exception as ex:
            raise RuntimeError(
                f"Error parsing Template {filename}: {ex}"
            ) from ex

    @property
    def environment(self):
        """
        Property to return the current Template Environment.
        """
        return self.env

    async def render(self, template: str, params: Optional[dict] = None) -> str:
        """Render.
        Renders a Jinja2 template using async-await syntax.
        """
        result = None
        if not params:
            params = {}
        try:
            template = self.env.get_template(str(template))
            result = await template.render_async(**params)
            return result
        except TemplateError as ex:
            raise ValueError(
                f"Template parsing error, template: {template}: {ex}"
            ) from ex
        except Exception as err:
            raise RuntimeError(
                f'NAV: Error rendering: {template}, error: {err}'
            ) from err


    async def view(
            self,
            filename: str,
            params: Optional[dict] = None,
            content_type: str = 'text/html',
            charset: Optional[str] = "utf-8",
            status: int = 200,
        ) -> web.Response:
        """view.
        description: view Method can return a Web Response from a Template content.
        Args:
            filename (str): Template name in template directory.
            params (Optional[dict], optional): Params passed to Template. Defaults to None.
            content_type (str, optional): Content Type of the Response. Defaults to 'text/html'.
            charset (Optional[str], optional): Charset of View. Defaults to "utf-8".
            status (int, optional): Optional HTTP method status. Defaults to 200 (OK).

        Raises:
            web.HTTPNotFound: When Template is missing or can't be parsed.
            web.HTTPBadRequest: When Template cannot be rendered.

        Returns:
            web.Response: an HTTP Web Response with Template result in the Body.
        """
        if not params:
            params = {}
        args = {
            "content_type": content_type,
            "headers": {
                'X-TEMPLATE': filename
            }
        }
        try:
            template = self.env.get_template(str(filename))
        except Exception as ex:
            # return 404 Not Found:
            args['headers']['X-TEMPLATE-ERROR'] = str(ex)
            raise web.HTTPNotFound(
                reason=f'Missing or Wrong Template file: {filename}: \n {ex!s}',
                **args
            )
        ## processing the template:
        try:
            result = await template.render_async(**params)
            response = {
                "content_type": content_type,
                "charset": charset,
                "status": status,
                "body": result
            }
            return web.Response(**response)
        except Exception as ex:
            args['headers']['X-TEMPLATE-ERROR'] = str(ex)
            raise web.HTTPBadRequest(
                reason=f'Error Parsing Template {filename}: {ex}',
                **args
            )
