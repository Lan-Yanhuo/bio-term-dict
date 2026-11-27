from pathlib import Path

# 与 GUI.py 同目录，基准目录一致
BASE_DIR = Path(__file__).resolve().parent

async def fsave(e,user,ui):
    ui.notify(f'The {e.file.name} has been uploaded.')
    target=BASE_DIR/'userfiles'/user/e.file.name
    await e.file.save(target)
    ui.notify(f'The {e.file.name} has saved.')

def fdisplay(ui,fl,user):
    view=ui.column().classes('w-full gap-2')
    req=ui.context.client.request
    pre=req.headers.get('X-Forwarded-Prefix','')+req.scope.get('root_path','')
    with view:
        if fl.name.lower().endswith('.pdf'):
            ui.element('iframe').props(f'src="{pre}/file/{user}/{fl.name}"').style(
             'width:100%;height:80vh;border:none')
        else:
            for ec in ['utf-8','gbk','utf-16']:
                try:
                    temp=fl.read_bytes().decode(ec)
                    break
                except UnicodeDecodeError:
                    pass
            ui.code(temp)

            
def fu(ui,user):
    ui.upload(on_upload=lambda e:fsave(e,user,ui),multiple=True,
          on_multi_upload=lambda:ui.notify(f'All files are updated.'),
          on_rejected=lambda: ui.notify('This file is too large! Please be less than 50 MB .'),
          max_file_size=50_000_000).classes('max-w-full')