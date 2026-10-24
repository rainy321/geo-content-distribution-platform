import builtins
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from utils.login_qrcode import decode_qrcode_from_path, print_terminal_qrcode


class LoginQrcodeRuntimeTests(unittest.TestCase):
    def test_clean_process_without_optional_packages_imports_saves_and_keeps_image_usable(self):
        script = r'''
import base64,contextlib,importlib.abc,io,json,sys
from pathlib import Path
class NoOptionalPackages(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'cv2','segno'}:
            raise ModuleNotFoundError('synthetic optional package missing',name=fullname.split('.')[0])
sys.meta_path.insert(0,NoOptionalPackages())
sys.path.insert(0,sys.argv[1])
from utils.login_qrcode import save_data_url_image,decode_qrcode_from_path,print_terminal_qrcode
assert 'cv2' not in sys.modules and 'segno' not in sys.modules
encoded='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=='
path=Path(sys.argv[2])/'normal-login.png'
assert save_data_url_image('data:image/png;base64,'+encoded,path)==path
assert path.read_bytes()==base64.b64decode(encoded)
assert decode_qrcode_from_path(path) is None
output=io.StringIO()
with contextlib.redirect_stdout(output):
    print_terminal_qrcode('synthetic login QR',path,'synthetic app')
assert str(path) in output.getvalue()
assert path.is_file()
print(json.dumps({'imported':True,'saved_image':True,'decode':None,'browser_scan_fallback':True}))
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable,'-I','-c',script,
                                     str(Path(__file__).resolve().parents[1]),directory],
                                    capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),{'imported':True,'saved_image':True,
                                                  'decode':None,'browser_scan_fallback':True})

    def test_installed_decoder_uses_existing_image_and_detector_without_changing_result(self):
        image = object()
        detector = SimpleNamespace(detectAndDecode=Mock(return_value=('synthetic QR content',None,None)))
        cv2 = SimpleNamespace(imread=Mock(return_value=image),QRCodeDetector=Mock(return_value=detector))
        path = Path('synthetic-existing.png')
        with patch.dict(sys.modules,{'cv2':cv2}):
            self.assertEqual(decode_qrcode_from_path(path),'synthetic QR content')
        cv2.imread.assert_called_once_with(str(path))
        detector.detectAndDecode.assert_called_once_with(image)

    def test_unreadable_image_preserves_existing_none_result(self):
        cv2 = SimpleNamespace(imread=Mock(return_value=None),QRCodeDetector=Mock())
        with patch.dict(sys.modules,{'cv2':cv2}):
            self.assertIsNone(decode_qrcode_from_path(Path('synthetic-unreadable.png')))
        cv2.QRCodeDetector.assert_not_called()

    def test_image_read_or_detector_initialization_errors_are_not_silenced(self):
        for field in ('imread','QRCodeDetector'):
            cv2 = SimpleNamespace(imread=Mock(return_value=object()),QRCodeDetector=Mock())
            getattr(cv2,field).side_effect = RuntimeError('synthetic real initialization failure')
            with self.subTest(field=field),patch.dict(sys.modules,{'cv2':cv2}):
                with self.assertRaisesRegex(RuntimeError,'initialization failure'):
                    decode_qrcode_from_path(Path('synthetic.png'))

    def test_broken_package_with_missing_transitive_dependency_is_not_treated_as_absent(self):
        original_import = builtins.__import__
        def broken_import(name,*args,**kwargs):
            if name in {'cv2','segno'}:
                raise ModuleNotFoundError('synthetic transitive dependency missing',name='unexpected_dependency')
            return original_import(name,*args,**kwargs)
        with patch('builtins.__import__',side_effect=broken_import):
            for action in (lambda:decode_qrcode_from_path(Path('synthetic.png')),
                           lambda:print_terminal_qrcode('synthetic',Path('synthetic.png'),'test')):
                with self.assertRaises(ModuleNotFoundError) as caught:
                    action()
                self.assertEqual(caught.exception.name,'unexpected_dependency')

    def test_installed_renderer_preserves_terminal_arguments(self):
        qrcode = SimpleNamespace(terminal=Mock())
        segno = SimpleNamespace(make=Mock(return_value=qrcode))
        with patch.dict(sys.modules,{'segno':segno}),contextlib.redirect_stdout(io.StringIO()):
            print_terminal_qrcode('synthetic content',Path('synthetic.png'),'test app',compact=False,border=2)
        segno.make.assert_called_once_with('synthetic content',error='L',boost_error=False)
        qrcode.terminal.assert_called_once_with(compact=False,border=2)

    def test_renderer_initialization_error_is_not_silenced(self):
        segno = SimpleNamespace(make=Mock(side_effect=ValueError('synthetic invalid QR content')))
        with patch.dict(sys.modules,{'segno':segno}),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError,'invalid QR content'):
                print_terminal_qrcode('synthetic',Path('synthetic.png'),'test app')


if __name__ == '__main__':
    unittest.main()
