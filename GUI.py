from nicegui import ui,app
from pathlib import Path
from datetime import datetime
from runcl import runcloud
from fupload import fu,fdisplay

# 所有数据路径都以本文件所在目录为基准，项目整体搬动不影响
BASE_DIR = Path(__file__).resolve().parent
app.add_static_files('/file',BASE_DIR/'userfiles',max_cache_age=0)

def sq(user)->dict:#this function is not well-defind right now
    folder=BASE_DIR/'userfiles'/user
    dic={}
    if not folder.exists():
        return dic
    for f in folder.iterdir():
        if f.is_file():
            dic[f.name]=f
    return dic

def wordlist():
    return ['a','b','c']#this function is not well-defind right now

def get_wordlist(word:str)->list:
    return ['ATP','bidinary','aldfsafagafdfkfjafjkd']#this function is not well-defind right now


def unc_sch(word:str,f)->str:#this function is not well-defind right now
    if True:
        ui.label('This word is not in this file.')
        return None
    unc=word
    f.open()
    ui.label('this function is not well-defined right now.')
    return unc

@ui.page('/{user}/file/{filnam}')
def file(user,filnam):
    word=ui.input(label='',placeholder='Please enter the word:',
     validation={'Too long!': lambda value: len(value) < 20})
    gotoword=lambda:unc_sch(word,sq(user)[filnam])
    ui.button('search',on_click=gotoword)
    word.on('keydown.enter',gotoword)
    fdisplay(ui,sq(user)[filnam],user)
    return   #this function has not finished.

@ui.page('/{user}/file')
def file_link(user):
    ui.link('upload new file',f'/{user}/file_upload')
    if sq(user):
        for filen in sq(user):
            ui.link(filen,f'/{user}/file/{filen}')


@ui.page('/{user}/file_upload')
def upl(user):
    fu(ui,user)

@ui.page('/{user}/wordlist')
def word_cloud(user):
    ui.label('word list:')
    wl=wordlist()
    for word in wl:
        ui.link(word,f'/{user}/wordlist/{word}')

@ui.page('/{user}/wordlist/{word}')
def cloud(user,word):
    ui.label("this is the relationship graph of %s."%word)
    wlist=get_wordlist(word)
    runcloud(wlist,ui,user,word)

@ui.page('/{user}')
def index(user):
    pat=BASE_DIR/'userfiles'/user
    with ui.expansion('Dictionary'):
        with ui.expansion('Biology'):
            ui.link('MB',f'/{user}/dictionary/MB')
        with ui.expansion('Chemistry'):
            ui.link('CC',f'/{user}/dictionary/CC')
        with ui.expansion('Physics'):
            ui.link('QM',f'/{user}/dictionary/QM')
    ui.link('wordlist',f'/{user}/wordlist')
    ui.link('file',f'/{user}/file')

@ui.page('/')
def sign_in():
    print(f'[{datetime.now():%Y-%m-%d %H:%M:%S}] 有人打开了首页  IP={ui.context.client.ip}')
    user=ui.input(label='User Name',placeholder='enter',
         validation={'Too long!': lambda value: len(value) < 20})
    gotodic=lambda: ui.navigate.to(f'/{user.value}')
    user.on('keydown.enter',gotodic)
    ui.button('enter',on_click=gotodic)
#ui.run(on_air=True)
ui.run()