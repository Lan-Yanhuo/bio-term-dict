import random
from html import escape
import math

#May I use the OOP to simplify the code,or follow this coding style?
sizex=1500
sizey=1100
JIGGLE=sizex/100
centx=sizex/2
centy=sizey/2
mlen=sizex/7
mwid=mlen/2
rads=mlen*1.25
vlim=sizex/400

def limv(v):
    if v>=vlim:
        return vlim
    elif v<=-vlim:
        return -vlim
    else:
        return v

def dist(l1,l2):
    return math.sqrt((l1[0]-l2[0])**2+(l1[1]-l2[1])**2)

def setf(d:dict)->None:
    for k in d:
        d[k]=False

def paint(img,wordlist,xlist,ylist,cword)->None:
    links=''.join(
        f'<line x1="{centx:.1f}" y1="{centy:.1f}" x2="{xlist[w]:.1f}" y2="{ylist[w]:.1f}" '
        f'stroke="#000000" stroke-width="{sizex/1500}"/>'
        for w in wordlist
    )
    cent=(f'<g transform="translate({centx:.1f},{centy:.1f})">'
    f'<rect x="{-mlen/2*1.25}" y="{-mwid/2*1.25}" width="{mlen*1.25}" height="{mwid*1.25}" '
    f'rx="{mlen/10}" ry="{mlen/10}" fill="#da3a3a"/>'
    f'<text x="0" y="0" text-anchor="middle" dominant-baseline="central" '
    f'font-size="{1/11*sizey/math.sqrt(len(cword))}" fill="#000000">{escape(cword)}</text>'
    f'</g>')
    img.content=links+cent+''.join(
        f'<g transform="translate({xlist[w]:.1f},{ylist[w]:.1f})">'
        f'<rect x="{-mlen/2}" y="{-mwid/2}" width="{mlen}" height="{mwid}" '
        f'rx="{mlen/10}" ry="{mlen/10}" fill="#25a7c8"/>'
        f'<text x="0" y="0" text-anchor="middle" dominant-baseline="central" '
        f'font-size="{16/220*sizey/math.sqrt(len(w))}" fill="#000000">{escape(w)}</text>'
        f'</g>'
        for w in wordlist
    )


def initial(wordlist,alist,centlistx,centlisty,vxlist,vylist,xlist,ylist):
    iter=0
    for word in wordlist:
        alist.setdefault(word,[0,0])
        vxlist.setdefault(word,0)
        vylist.setdefault(word,0)
        centlistx.setdefault(word,centx+rads*math.cos(iter*2*math.pi/len(wordlist)))
        centlisty.setdefault(word,centy+rads*math.sin(iter*2*math.pi/len(wordlist)))
        xlist.setdefault(word,centx+rads*math.cos(iter*2*math.pi/len(wordlist)))
        ylist.setdefault(word,centy+rads*math.sin(iter*2*math.pi/len(wordlist)))
        iter+=1


def jiggle(wordlist,img,i,alist,xlist,ylist,vxlist,vylist,is_click,centlistx,centlisty,cword)->None:
    for word in wordlist:
        if is_click[word]:
            continue
        if i[0]%25==0:
            i[0]=0
            alist[word]=[random.uniform(-JIGGLE,JIGGLE),random.uniform(-JIGGLE,JIGGLE)]
        xlist[word]+=vxlist[word]/15
        ylist[word]+=vylist[word]/15
        vxlist[word]+=alist[word][0]/60
        vylist[word]+=alist[word][1]/60
        vxlist[word]=limv(vxlist[word])
        vylist[word]=limv(vylist[word])
        if abs(xlist[word]-centlistx[word])>=JIGGLE:
            vxlist[word]=-0.7*vxlist[word]
        if abs(ylist[word]-centlisty[word])>=JIGGLE:
            vylist[word]=-0.7*vylist[word]
    i[0]=1+i[0]
    paint(img,wordlist,xlist,ylist,cword)


def mouse(e,wordlist,img,last_p,xlist,ylist,is_click,centlistx,centlisty,ui,user,click,cword):
    for word in wordlist:
        if e.type=='pointerup' or e.type=='pointercancel':
            if e.type=='pointerup' and is_click[word] and dist(click[word],[xlist[word],ylist[word]])<=3:
                ui.navigate.to(f'/{user}/wordlist/{word}')
                return
            is_click[word]=False
            continue
        if e.type=='pointerdown':
            if abs(e.image_x-xlist[word])>mlen/2 or abs(e.image_y-ylist[word])>mwid/2:
                is_click[word]=False
                continue
            is_click[word]=True
            click[word]=[e.image_x,e.image_y]
            last_p[word]=[e.image_x,e.image_y]
            centlistx[word]=xlist[word]=e.image_x
            centlisty[word]=ylist[word]=e.image_y
            break
        if e.type=='pointermove' and is_click[word]:
            if e.buttons==0:
                setf(is_click)
                break
            xlist[word]+=e.image_x-last_p[word][0]
            ylist[word]+=e.image_y-last_p[word][1]
            centlistx[word]+=e.image_x-last_p[word][0]
            centlisty[word]+=e.image_y-last_p[word][1]
            last_p[word][0]=e.image_x
            last_p[word][1]=e.image_y
    paint(img,wordlist,xlist,ylist,cword)
    return


def runcloud(wordlist,ui,user,cword):
    alist={}
    centlistx={}
    centlisty={}
    xlist={}
    ylist={}
    vxlist={}
    vylist={}
    i=[0]
    click={k:[0,0] for k in wordlist}
    is_click={k:False for k in wordlist}
    last_p={word:[0,0] for word in wordlist}
    initial(wordlist,alist,centlistx,centlisty,vxlist,vylist,xlist,ylist)
    ratio=sizex/sizey
    img=ui.interactive_image(
    content='',
    size=(sizex,sizey),
    on_mouse=lambda e:mouse(e,wordlist,img,last_p,xlist,ylist,is_click,centlistx,centlisty,ui,user,click,cword),
    events=['pointerdown','pointermove','pointerup','pointercancel'],
    sanitize=False,
    ).style(f'width: min(100%, calc((100vh - 96px) * {ratio:.6f})); height: auto; margin: auto; touch-action: none')
    ui.timer(1/60,lambda:jiggle(wordlist,img,i,alist,xlist,ylist,vxlist,vylist,is_click,centlistx,centlisty,cword))# about 60 FPS
