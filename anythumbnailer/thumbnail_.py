
from __future__ import absolute_import

from io import BytesIO
# import mimetypes
import magic
import os
import re
import shutil
import tempfile

from .sh_utils import run

try:
    unicode = unicode
except NameError:
    # 'unicode' is undefined, must be Python 3
    str = str
    unicode = str
    bytes = bytes
    basestring = (str,bytes)
else:
    # 'unicode' exists, must be Python 2
    str = str
    unicode = unicode
    bytes = str
    basestring = basestring

__all__ = ['create_thumbnail']


def create_thumbnail(source_filename, dimensions=None, **kwargs):
    assert dimensions is None
    # mime_type, encoding = mimetypes.guess_type(source_filename, strict=False)
    mime = magic.Magic(mime=True)
    mime_type = mime.from_file(source_filename)
    if (mime_type is None) and ('.' in source_filename):
        extension = source_filename.rsplit('.', 1)[-1].lower()
        mime_type = mimetypes_by_extension.get(extension)
    if mime_type is None:
        return None
    thumbnailer = thumbnailer_for(mime_type)
    if thumbnailer is None:
        return None
    return thumbnailer.thumbnail(source_filename, dimensions, **kwargs)


class Thumbnailer(object):
    def is_available(self):
        if hasattr(self, 'executables'):
            executables = self.executables
        else:
            executables = (self.executable, )
        for command_path in executables:
            command = command_path.split(' ', 1)[0]
            if not os.path.exists(command):
                return False
        return True

    def thumbnail(self, source_filename_or_fp, dimensions=None, **kwargs):
        raise NotImplementedError()


class PNMToImage(Thumbnailer):
    pnm_to_png = ((os.path.exists('/usr/bin/pnmtopng') and '/usr/bin/pnmtopng') or
                  (os.path.exists('/usr/local/bin/pnmtopng') and '/usr/local/bin/pnmtopng'))
    pnm_to_jpg = ((os.path.exists('/usr/bin/pnmtojpeg') and '/usr/bin/pnmtojpeg') or
                  (os.path.exists('/usr/local/bin/pnmtojpeg') and '/usr/local/bin/pnmtojpeg'))

    executables = (pnm_to_png, pnm_to_jpg)

    def pipe_args(self, dimensions=None, output_format='jpg'):
        assert dimensions is None
        executable = self.pnm_to_jpg if (output_format == 'jpg') else self.pnm_to_png
        return (
            executable,
        )

    def thumbnail(self, source_filename_or_fp, **kwargs):
        return run(self.pipe_args(**kwargs), input_=source_filename_or_fp)


# pdftoppm 0.12.4 (CentOS 6.5) bails out if the PDF contents are transferred
# via stdin. pdftoppm 0.24.3 (Fedora 20) works fine though...
class Poppler(Thumbnailer):
    pdf_to_ppm = ((os.path.exists('/usr/bin/pdftoppm') and '/usr/bin/pdftoppm') or
                  (os.path.exists('/usr/local/bin/pdftoppm') and '/usr/local/bin/pdftoppm'))
    executables = (pdf_to_ppm, ) + PNMToImage.executables

    def _args(self, source_filename=None, output_filename=None, dimensions=None, page=1):
        assert dimensions is None
        command = (
            self.pdf_to_ppm,
                # '-scale-to', str(2048),
                '-f', str(page),
                '-l', str(page),
        )
        if source_filename is not None and output_filename is not None:
            command += (source_filename, output_filename)
        return command


    def thumbnail(self, source_filename_or_fp, dimensions=None, page=1, output_format='jpg'):
        assert dimensions is None
        temp_fp = None
        pnm_fp = None
        temp_ppm_tpl_file = None
        try:
            if not hasattr(source_filename_or_fp, 'read'):
                filename = source_filename_or_fp
            else:
                temp_fp = tempfile.NamedTemporaryFile(delete=True)
                temp_fp.write(source_filename_or_fp.read())
                temp_fp.flush()
                filename = temp_fp.name
            temp_ppm_tpl_file = tempfile.NamedTemporaryFile(delete=True)

            pdftoppm_args = self._args(source_filename=filename, output_filename=temp_ppm_tpl_file.name,
                                       dimensions=dimensions, page=page)

            run(pdftoppm_args)

            last_index = temp_ppm_tpl_file.name.rfind('/')

            folderpath = temp_ppm_tpl_file.name[:last_index]
            fn = temp_ppm_tpl_file.name[last_index+1:]

            found = [filename for filename in os.listdir(folderpath) if filename.startswith(fn) and filename.endswith('1.ppm')]

            pnm_filename = f'{folderpath}/{found[0]}'

            with open(pnm_filename, 'rb+') as pnm_fp:
                pnm_converter_args = PNMToImage().pipe_args(dimensions=dimensions, output_format=output_format)
                ret = run(pnm_converter_args, input_=pnm_fp)

            return ret
        finally:
            if temp_fp is not None:
                temp_fp.close()
            if temp_ppm_tpl_file is not None:
                temp_ppm_tpl_file.close()
            if pnm_fp is not None:
                pnm_fp.close()
                os.remove(pnm_filename)


class FileOutputThumbnailer(Thumbnailer):
    output_pattern = None

    def _args(self, source_filename, output_filename):
        raise NotImplementedError()

    def _find_output_filename(self, temp_dir, output_format):
        # As a rough heuristic we'll pick the biggest one which probably
        # contains the most interesting data.
        file_paths = []
        for filename in os.listdir(temp_dir):
            if filename.endswith('.'+output_format):
                file_paths.append(os.path.join(temp_dir, filename))
        if len(file_paths) == 0:
            return None
        files_with_size = [(os.stat(path).st_size, path) for path in file_paths]
        return sorted(files_with_size)[-1][1]

    def thumbnail(self, source_filename, dimensions=None, output_format='jpg'):
        assert dimensions is None
        try:
            temp_dir = tempfile.mkdtemp()
            temp_file = os.path.join(temp_dir, self.output_pattern+output_format)
            output_fp = run(self._args(source_filename, temp_file))
            if output_fp is None:
                return None
            output_filename = self._find_output_filename(temp_dir, output_format)
            if output_filename is None:
                return None

            with open(output_filename, 'rb') as of:
                of_contents = of.read()
                
            return BytesIO(of_contents)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class Unoconv(FileOutputThumbnailer):
    executable = ((os.path.exists('/usr/bin/unoconv') and '/usr/bin/unoconv') or
                  (os.path.exists('/usr/local/bin/unoconv') and '/usr/local/bin/unoconv'))
    output_pattern = 'document.'

    def _args(self, source_filename, output_filename):
        return (
            self.executable,
            '-f', 'pdf',
            # it seems that LibreOffice 4.0/4.1 has a bug somewhere in pyuno
            # (or the LibreOffice core itself) when it should output to stdout:
            # https://github.com/dagwieers/unoconv/issues/66
            # http://thread.gmane.org/gmane.linux.debian.devel.bugs.general/1074109/focus=1074227
            # falling back to a temporary file which (strangely) works.
            # "--stdout" works fine in LibreOffice 4.2
            # '--stdout',
            '--output='+output_filename,
            source_filename,
        )

    def thumbnail(self, source_filename, dimensions=None, page=1, output_format='jpg'):
        pdf_fp = super(Unoconv, self).thumbnail(source_filename, dimensions=dimensions,
            output_format='pdf')
        if pdf_fp is None:
            return None
        pdf_thumbnailer = thumbnailer_for('application/pdf')
        return pdf_thumbnailer.thumbnail(pdf_fp, dimensions=dimensions,
            page=page, output_format=output_format)


class ImageMagick(FileOutputThumbnailer):
    # some image formats might contain multiple pages and/or layers and
    # ImageMagick will create multiple output files in that case.
    executable = ((os.path.exists('/usr/bin/convert') and '/usr/bin/convert') or
                  (os.path.exists('/usr/local/bin/convert') and '/usr/local/bin/convert'))
    output_pattern = 'output.'

    def _args(self, source_filename, output_filename):
        return (
            self.executable,
            "-strip",
            "-limit", "area", "10MB",
            "-limit", "disk", "100MB",
            "-thumbnail", "160x160",
            source_filename,
            output_filename,
        )


class ffmpeg(FileOutputThumbnailer):
    executable = ((os.path.exists('/usr/bin/ffmpeg') and '/usr/bin/ffmpeg') or
                  (os.path.exists('/usr/local/bin/ffmpeg') and '/usr/local/bin/ffmpeg'))
    output_pattern = 'output%02d.'

    def _args(self, source_filename, output_filename):
        return (
            self.executable,
            '-v', 'quiet',
            '-ss', '3',
            '-i', source_filename,
            '-frames:v', '5',
            '-r', '1/10',
            '-vsync', 'vfr',
            output_filename,
        )


class PS2PDF(Thumbnailer):
    executable = ((os.path.exists('/usr/bin/ps2pdf') and '/usr/bin/ps2pdf') or
                  (os.path.exists('/usr/local/bin/ps2pdf') and '/usr/local/bin/ps2pdf'))

    def pipe_args(self, dimensions=None):
        assert dimensions is None
        return (
            self.executable,
            '-',
        )

    def thumbnail(self, source_filename_or_fp, dimensions=None, output_format='jpg'):
        pdf_fp = run(self.pipe_args(dimensions=dimensions), input_=source_filename_or_fp)
        if pdf_fp is None:
            return None
        elif output_format == 'pdf':
            return pdf_fp
        pdf_thumbnailer = thumbnailer_for('application/pdf')
        return pdf_thumbnailer.thumbnail(pdf_fp, dimensions=dimensions,
            output_format=output_format)



thumbnailers = {
    'application/postscript': PS2PDF,
    'application/pdf': Poppler,

    # "Office" documents
    'application/msword': Unoconv, # doc
    re.compile('^'+re.escape('application/vnd.ms-')): Unoconv, # xls/ppt
    re.compile('^'+re.escape('application/vnd.openxmlformats-officedocument.')): Unoconv, # docx, pptx, xlsx
    'application/vnd.ms-excel.sheet.macroEnabled.12': Unoconv, # xlsm: xlsx with macros
    'application/octet-stream': Unoconv, # Some docx files get saved to this somehow...

    # specific mime types have precedence over regexes so PNMToImage will be
    # preferred over ImageMagick for pnm files.
    'image/x-portable-pixmap': PNMToImage,

    # all image-like formats, also uncommon ones like
    #    .psd -> image/vnd.adobe.photoshop
    #    .tga -> image/x-targa
    re.compile('^image/'): ImageMagick,

    re.compile('^video/'): ffmpeg, # videos
    # ogg is a container format both for audio (Ogg Vorbis) and videos (Ogg
    # Theora). Python's mimetypes library does not differentiate between these
    # two so we just try ffmpeg for both. It'll just fail for audio but that
    # shouldn't do any harm.
    'audio/ogg': ffmpeg,
}

# Python's mimetypes library does not detect all file formats so we have a
# fallback to "detect" a mime type based on the file extension.
mimetypes_by_extension = {
    'f4v': 'video/x-flv',
}

def thumbnailer_for(mime_type):
    thumbnailer = thumbnailers.get(mime_type)
    if thumbnailer is None:
        regex_thumbnailers = filter(lambda key: not isinstance(key, basestring), thumbnailers)
        for regex in regex_thumbnailers:
            if regex.match(mime_type):
                thumbnailer = thumbnailers[regex]
                break
        else:
            return None
    if not thumbnailer().is_available():
        return None
    return thumbnailer()

