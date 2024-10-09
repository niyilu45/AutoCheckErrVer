import argparse
import os
import io
import json
import sys
import re
import os
import math
import shutil
import time
import toml
from pprint import pprint
import subprocess
import threading


def CdAndReturnCurPath(tarPath):
    curPath = os.getcwd()
    os.chdir(tarPath)
    return curPath

def RunCmd(cmdStr, *, logEn=True, blockEn=True):
    # logEn will enable block by lib subprocess
    # blockEn will wait for cmd finish.
    process = subprocess.Popen(cmdStr.split(' '), shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE , text=True)
    output =None
    err = None
    if blockEn:
        process.wait()
    else:
        logEn = False
    if logEn:
        output, err = process.communicate()
    return process, output, err

def CheckCmdDone(process):
    done = False
    if process.poll() is not None:
        done = True
    return done
def WaitCmdDone(process):
    process.wait()

def ListAllVer(progPath, verSt, verEd):
    curPath = CdAndReturnCurPath(progPath)
    cmdStr = f'git log --oneline {verSt}..{verEd}'
    print(f"using cmd: {cmdStr}")
    _, log, err = RunCmd(cmdStr)
    vers = [verSt]
    for item in log.split('\n')[::-1]:
        if not item:
            continue
        vers.append(item.split(' ')[0])

    CdAndReturnCurPath(curPath)
    return vers

def CheckAllDone(retDict):
    allDone = True
    for caseName in retDict:
        if not retDict[caseName]['done']:
            allDone = False
            break
    return allDone

class RunStat():
    noRun   = 0
    pend    = 1
    run     = 2
    retIdle = 3
    done    = 4
class RetStat():
    unknown = 0
    good    = 1
    bad     = 2
class RunTools():
    local = 0
    dsub  = 1

def JudgeRunVersIdx(versDict, verNum, runVerNum):
    runVerNumRest = runVerNum
    runIdxs = []
    if runVerNumRest > 0 and versDict['verIdx_0']['run'] == RunStat.noRun:
        runIdxs.append(0)
    if runVerNumRest > 0 and versDict[f'verIdx_{verNum-1}']['run'] == RunStat.noRun:
        runIdxs.append(verNum - 1)

    if runVerNumRest > 0:
        verRestNum = 0
        verSt = 0
        verEd = 0
        for idx in range(verNum):
            if versDict[f'verIdx_{idx}']['ret'] == RetStat.good:
                verSt = idx
            if versDict[f'verIdx_{idx}']['ret'] == RetStat.bad:
                verEd = idx
                break
        verSt = verSt + 1
        verLen = verEd - verSt
        verStep = max(1, math.ceil(verLen / runVerNum))
        runIdxs = runIdxs + list(range(verSt,verEd,verStep))

    return runIdxs

def PrepareExecAndRetPath(caseName, progPath, casePaths, retPath, ver, *, exeName = 'test.exe', exeSaveEn=True):
    progPath = os.path.abspath(progPath)
    casePaths = [os.path.abspath(x) for x in casePaths]
    retPath = os.path.abspath(retPath)
    # 2) -1] build and copy exeFolder if needed
    buildEn = True
    exeFd = os.path.join(retPath, f"ExeFdAll", f"{ver}")
    exePath = os.path.join(exeFd, exeName)
    if os.path.exists(exeFd) and os.path.isfile(exePath):
        buildEn = False
    if buildEn:
        try:
            os.makedirs(exeFd)
        except Exception as e:
            pass
        curPath = CdAndReturnCurPath(progPath)
        RunCmd('make')
        shutil.copy(exeName, exeFd)
        CdAndReturnCurPath(curPath)

    # 2) -2] create tc result folders
    retFd = os.path.join(retPath, 'Result', caseName, f'{ver}')
    if os.path.exists(retFd):
        shutil.rmtree(retFd)
    os.makedirs(retFd)
    sys.stdout.flush()
    os.symlink(exePath, os.path.join(retFd, exeName))

    # 2) -3] prepare case
    for casePath in casePaths:
        shutil.copy(casePath, retFd)

    return retFd

def RunProgVer(caseName, ver, *, exeSaveEn=True):
    progPath = g_retAll[caseName]['progPath']
    casePaths = g_retAll[caseName]['casePaths']
    retPath  = g_retAll[caseName]['retPath']
    exeName  = g_retAll[caseName]['exeName']
    # 1) Checkout ver
    curPath = CdAndReturnCurPath(progPath)
    RunCmd('git reset --hard')
    RunCmd(f'git checkout {ver}')
    CdAndReturnCurPath(curPath)

    # 2) for usr's need
    if g_retAll[caseName]['scriptExt']:
        RunCmd(g_retAll[caseName]['scriptExt'])

    # 3) Prepare exec folder and result folder
    retCasePath = PrepareExecAndRetPath(caseName, progPath, casePaths, retPath, ver, exeName = exeName, exeSaveEn=exeSaveEn)

    # 4) run
    curPath = CdAndReturnCurPath(retCasePath)
    process, log, err = RunCmd(f"./{exeName}", blockEn=False)
    CdAndReturnCurPath(curPath)

    return process, retCasePath

def ThreadRunCases(runCasesArgs):
    while True:
    # 1) Get lock
        g_lockRetStatDict.acquire()
    # 2) Check runing vers and to run vers
        toRunVers = {}
        curRunCasesNum = {
            'tot': 0,
        }
        for caseName in g_retAll.keys():
            toRunVers[caseName] = []
            curRunCasesNum[caseName] = 0
            versPtr = g_retAll[caseName]['vers']
            for ver in versPtr.keys():
                verPtr = versPtr[ver]
                if verPtr['run'] == RunStat.run:
                    curRunCasesNum['tot'] = curRunCasesNum['tot'] + 1
                    curRunCasesNum[caseName] = curRunCasesNum[caseName] + 1
                if verPtr['run'] == RunStat.pend:
                    toRunVers[caseName].append(ver)
    # 3) run vers
        for caseName in g_retAll.keys():
            for ver in toRunVers[caseName]:
                if curRunCasesNum['tot'] > runCasesArgs['maxRunCases']:
                    continue
                if curRunCasesNum[caseName] > runCasesArgs['maxRunCasePerVer']:
                    continue
                verInfoPtr = g_retAll[caseName]['vers'][ver]
                runMethod  = g_retAll[caseName]['runMethod']
                stat, retCasePath = RunProgVer(caseName, verInfoPtr['ver'])
                verInfoPtr['run'] = RunStat.run
                verInfoPtr['process'] = stat
                verInfoPtr['retPath'] = retCasePath

                curRunCasesNum['tot'] = curRunCasesNum['tot'] + 1
                curRunCasesNum[caseName] = curRunCasesNum[caseName] + 1
                print(f"Running case: {caseName}, ver: {verInfoPtr['ver']}")
                sys.stdout.flush()

    # 4) release lock
        allDone = CheckAllDone(g_retAll)
        g_lockRetStatDict.release()
    # 5) check all cases run over or not
        if allDone:
            break
        time.sleep(1)
        pass

def ThreadCheckCasesRunStat():
    while True:
    # 1) Get lock
        g_lockRetStatDict.acquire()
    # 2) Check run stat
        for caseName in g_retAll.keys():
            runMethod = g_retAll[caseName]['runMethod']
            versPtr = g_retAll[caseName]['vers']
            for ver in versPtr.keys():
                verPtr = versPtr[ver]
                if verPtr['run'] != RunStat.run:
                    continue
                if runMethod == RunTools.local:
                    process = verPtr['process']
                    if CheckCmdDone(process):
                        verPtr['run'] = RunStat.retIdle

    # 4) release lock
        allDone = CheckAllDone(g_retAll)
        g_lockRetStatDict.release()
    # 5) check all cases run over or not
        if allDone:
            break
        time.sleep(3)
    pass

def ThreadRetSumAndChooseRunVers(runVerNum):
    cnt = 0
    while True:
        cnt = cnt + 1
    # 1) Get lock
        g_lockRetStatDict.acquire()
    # 3) Wait for all vers run over
        verRunOver = {}
        for caseName in g_retAll.keys():
            if g_retAll[caseName]['done']:
                continue
            verRunOver[caseName] = True
            versPtr = g_retAll[caseName]['vers']
            for ver in versPtr.keys():
                verPtr = versPtr[ver]
                if verPtr['run'] == RunStat.run or verPtr['run'] == RunStat.pend:
                    verRunOver[caseName] = False
                    break
    # 4) Check ver is good or bad
        for caseName in g_retAll.keys():
            if g_retAll[caseName]['done'] or not verRunOver[caseName]:
                continue
            versPtr = g_retAll[caseName]['vers']
            for ver in versPtr.keys():
                verPtr = versPtr[ver]
                if verPtr['run'] != RunStat.retIdle:
                    continue
                verPtr['run'] = RunStat.done
                if verPtr['ret'] != RetStat.unknown:
                    continue
                # TODO need to add result check function
                ret = RetStat.good
                verPtr['ret'] = ret
    # 5) Judge next run vers or give which is errVer
        for caseName in g_retAll.keys():
            if g_retAll[caseName]['done'] or not verRunOver[caseName]:
                continue
            versPtr = g_retAll[caseName]['vers']
            toRunVerIdxs = JudgeRunVersIdx(versPtr, g_retAll[caseName]['verNum'], runVerNum)
            if not toRunVerIdxs:
                g_retAll[caseName]['done'] = True
                # get bad ver
                for idx in range(g_retAll[caseName]['verNum']):
                    if versPtr[f'verIdx_{idx}']['ret'] == RetStat.bad:
                        g_retAll[caseName]['badVer'] = versPtr[f'verIdx_{idx}']['verTag']
                        break
                print(f"case [{caseName}] had find badVer in: {g_retAll[caseName]['badVer']}")
                sys.stdout.flush()
            else:
                versPtr = g_retAll[caseName]['vers']
                for idx in toRunVerIdxs:
                    versPtr[f"verIdx_{idx}"]['run'] = RunStat.pend

    # 6) release lock
        allDone = CheckAllDone(g_retAll)
        g_lockRetStatDict.release()
    # 7) check all cases run over or not
        if allDone:
            pprint(g_retAll)
            print('Ret Sum All done exit')
            break
        if cnt > 10:
            print('Ret Sum cnt done exit')
            # break
        time.sleep(5)
    pass

def ThreadReport(reportPath):
    cnt = 0
    while True:
        cnt = cnt+1
    # 1) Get lock
        g_lockRetStatDict.acquire()
    # 2) report
        if not os.path.exists(os.path.dirname(reportPath)):
            os.makedirs(os.path.dirname(reportPath))
        with open(reportPath, 'w') as fp:
            toml.dump(g_retAll, fp)
    # 4) release lock
        allDone = CheckAllDone(g_retAll)
        g_lockRetStatDict.release()
        # print(f"allDone: {allDone}")
    # 5) check all cases run over or not
        if allDone:
            break

        # for debug
        # if cnt > 6:
            # for caseName in g_retAll.keys():
                # versPtr = g_retAll[caseName]['vers']
                # for ver in versPtr.keys():
                    # versPtr[ver]['run'] = RunStat.retIdle
            # break
        # if cnt > 30:
            # break
        time.sleep(1)
    pass

# TODO need an interface
def FindErrVers(runArgs):
    """
        input example:
            runArgs = {
                'progPath': "input/prog"
                'casePaths': [input/case/simParameter.txt, input/case/config.txt],
                'retPath' : "output"
                'exeName' : 'test.exe'
                'maxRunCases'      : 1000
                'maxRunCasePerVer' : 4
                'runVerNum'        : 2
                'exeSaveEn'        : True,
                'scriptExt'   : None, # to modify codes etc.
                'preHookFunc'      : None, # to copy files
                'preHookFuncArgs'  : {},
                'postHookFunc'      : None, # to check result
                'postHookFuncArgs'  : {},
                'runMethod'        : RunTools.local,
                'caseNames': {
                    'caseName1':{
                        'verSt' : 'b7286efd3ff2458bbcb35358e51ec23780e5480e'
                        'verEd' : '10c8b80dc9d5727f08093d7ec22fb1a0b6d98822'
                    }
                }
            }
    """
    pass
# TODO need add scriptExt and Result Check, in  anthor file

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description = 'none')
    parser.add_argument('-s', '--sel', type = int, default=0, help='0:E, 1:F')
    parser.add_argument('-v', '--vr', type = int, default=0, help='1:vr sort, 0:normal sort')
    args = parser.parse_args()

    # 0) set resource limit
    maxRunCases = 1000
    maxRunCasePerVer = 4
    runVerNum = 2
    caseName = 'case1'
    progPath = "input/prog"
    retPath = "output"
    exeName = 'test.exe'
    verSt = 'b7286efd3ff2458bbcb35358e51ec23780e5480e'
    verEd = '10c8b80dc9d5727f08093d7ec22fb1a0b6d98822'

    global g_lockRetStatDict
    g_lockRetStatDict = threading.Lock()

    # 1) list all ver, set last ver is good
    allVers = ListAllVer(progPath, verSt, verEd)
    allVersNum = len(allVers)
    pprint(allVers)
    # init result dict
    global g_retAll
    g_retAll = {caseName: {
        "verNum"    : allVersNum,
        "toRunVers" : [],
        "done"      : False,
        "runMethod" : RunTools.local,
        "vers"      : {},
        "badVer"    : "",
        'casePaths' : [],
        'progPath'  : os.path.abspath(progPath),
        'retPath'   : os.path.abspath(retPath),
        'exeName'   : exeName,
        'scriptExt'   : None,
    }}
    for idx, ver in enumerate(allVers):
        g_retAll[caseName]['vers'][f"verIdx_{idx}"]= {
            "ver": ver,
            "verTag": ver,
            "run": RunStat.noRun,
            "ret": RetStat.unknown,
            "process": None,
            "retPath": None,
            "errVer": None,
        }
        if idx == 0:
            g_retAll[caseName]['vers'][f"verIdx_{idx}"]['ret'] = RetStat.good
            # g_retAll[caseName][f"verIdx_{idx}"]['run'] = RunStat.pend
        if idx == allVersNum-1:
            g_retAll[caseName]['vers'][f"verIdx_{idx}"]['ret'] = RetStat.bad
            # g_retAll[caseName][f"verIdx_{idx}"]['run'] = RunStat.pend
    pprint(g_retAll)

    # 2) set threads paras
    reportPath = "output/report.toml"
    reportPath = os.path.abspath(reportPath)
    runCasesArgs = {
        'maxRunCasePerVer' : maxRunCasePerVer,
        'maxRunCases'      : maxRunCases,
        'exeSaveEn'        : True,
        'preHookFunc'      : None,
        'preHookFuncArgs'  : {},
        'runMethod'        : RunTools.local,
    }
    threadRunCases = threading.Thread(target=ThreadRunCases, args=(runCasesArgs,))
    threadRetCheck = threading.Thread(target=ThreadCheckCasesRunStat, args=())
    threadRetSum = threading.Thread(target=ThreadRetSumAndChooseRunVers, args=(runVerNum,))
    threadReport = threading.Thread(target=ThreadReport, args=(reportPath,))

    # 3) start threads
    print(f"Plz Check runing stat in: {reportPath}")
    print(f"Running ...")
    sys.stdout.flush()
    threadRunCases.start()
    threadRetCheck.start()
    threadReport.start()
    threadRetSum.start()
    # 4) waiting for end
    threadRunCases.join()
    threadRetCheck.join()
    threadReport.join()
    threadRetSum.join()
    # 5) report final result
    print('all is over!')
    print('hello')
