# -*- coding: utf-8 -*-
"""
DataManager.py - DataManager, FileHandle, and DirHandle classes 
Copyright 2010  Luke Campagnola
Distributed under MIT/X11 license. See license.txt for more infomation.

These classes implement a data management system that allows modules
to easily store and retrieve data files along with meta data. The objects
probably only need to be created via functions in the Manager class.
"""

if __name__ == '__main__':
    import os, sys
    path = os.path.dirname(os.path.abspath(__file__))
    sys.path.append(os.path.join(path, '..', '..'))

#from __future__ import with_statement
import threading, os, re, sys, shutil
##  import fcntl  ## linux only?
from functions import strncmp
from configfile import *
#from metaarray import MetaArray
import time
from Mutex import Mutex, MutexLocker
from pyqtgraph import SignalProxy, ProgressDialog
#from pyqtgraph.ProgressDialog import ProgressDialog
from PyQt4 import QtCore, QtGui
if not hasattr(QtCore, 'Signal'):
    QtCore.Signal = QtCore.pyqtSignal
    QtCore.Slot = QtCore.pyqtSlot
#from lib.filetypes.FileType import *
import lib.filetypes as filetypes
from debug import *
import copy
import advancedTypes


def abspath(fileName):
    """Return an absolute path string which is guaranteed to uniquely identify a file."""
    return os.path.normcase(os.path.abspath(fileName))


def getDataManager():
    inst = DataManager.INSTANCE
    if inst is None:
        raise Exception('No DataManger created yet!')
    return inst

def getHandle(fileName):
    return getDataManager().getHandle(fileName)

def getDirHandle(fileName, create=False):
    return getDataManager().getDirHandle(fileName, create=create)

def getFileHandle(fileName):
    return getDataManager().getFileHandle(fileName)

def cleanup():
    """
    Free memory by deleting cached handles that are not in use elsewhere.
    This is useful in situations where a very large number of handles are
    being created, such as when scanning through large data sets.
    """
    getDataManager().cleanup()


class DataManager(QtCore.QObject):
    """Class for creating and caching DirHandle objects to make sure there is only one manager object per file/directory. 
    This class is (supposedly) thread-safe.
    """
    
    INSTANCE = None
    
    def __init__(self):
        QtCore.QObject.__init__(self)
        if DataManager.INSTANCE is not None:
            raise Exception("Attempted to create more than one DataManager!")
        DataManager.INSTANCE = self
        self.cache = {}
        #self.lock = threading.RLock()
        self.lock = Mutex(QtCore.QMutex.Recursive)
        
    def getDirHandle(self, dirName, create=False):
        with self.lock:
            dirName = os.path.abspath(dirName)
            #if not (create or os.path.isdir(dirName)):
            #    if not os.path.exists(dirName):
            #        raise Exception("Directory %s does not exist" % dirName)
            #    else:
            #        raise Exception("Not a directory: %s" % dirName)
            if not self._cacheHasName(dirName):
                self._addHandle(dirName, DirHandle(dirName, self, create=create))
            return self._getCache(dirName)
        
    def getFileHandle(self, fileName):
        with self.lock:
            fileName = os.path.abspath(fileName)
            #if not os.path.exists(fileName):
            #    raise Exception("File %s does not exist" % fileName)
            #if not os.path.isfile(fileName):
            #    raise Exception("Not a regular file: %s" % fileName)
            if not self._cacheHasName(fileName):
                self._addHandle(fileName, FileHandle(fileName, self))
            return self._getCache(fileName)
        
    def getHandle(self, fileName):
        """Return a FileHandle or DirHandle for the given fileName. 
        If the file does not exist, this method returns a FileHandle.
        """
        fn = os.path.abspath(fileName)
        #if not os.path.exists(fn):
            #raise Exception("File '%s' does not exist." % fn)
        if os.path.isdir(fn):
            return self.getDirHandle(fileName)
        else:
            return self.getFileHandle(fileName)
        
    def cleanup(self):
        """Attempt to free memory by allowing python to collect any unused handles."""
        import gc
        with self.lock:
            tmp = weakref.WeakValueDictionary(self.cache)
            self.cache = None
            gc.collect()
            self.cache = dict(tmp)

    def _addHandle(self, fileName, handle):
        """Cache a handle and watch it for changes"""
        #print "*******data manager caching new handle", handle
        self._setCache(fileName, handle)
        ## make sure all file handles belong to the main GUI thread
        app = QtGui.QApplication.instance()
        if app is not None:
            handle.moveToThread(app.thread())
        ## No signals; handles should explicitly inform the manager of changes
        #QtCore.QObject.connect(handle, QtCore.SIGNAL('changed'), self._handleChanged)
        
        
    def _handleChanged(self, handle, change, *args):
        with self.lock:
            #print "Manager handling changes", handle, change, args
            if change == 'renamed' or change == 'moved':
                oldName = args[0]
                newName = args[1]
                #print oldName, newName
                ## Inform all children that they have been moved and update cache
                tree = self._getTree(oldName)
                #print "  Affected handles:", tree
                for h in tree:
                    ## Update key to cached handle
                    newh = os.path.abspath(os.path.join(newName, h[len(oldName+os.path.sep):]))
                    #print "update key %s, (newName=%s, )" % (newh, newName)
                    self._setCache(newh, self._getCache(h))
                    
                    ## If the change originated from h's parent, inform it that this change has occurred.
                    if h != oldName:
                        #print "  Informing", h, oldName
                        self._getCache(h)._parentMoved(oldName, newName)
                    self._delCache(h)
                
            elif change == 'deleted':
                oldName = args[0]

                ## Inform all children that they have been deleted and remove from cache
                tree = self._getTree(oldName)
                for path in tree:
                    self._getCache(path)._deleted()
                    self._delCache(path)
                    
                #self._delCache(oldName)
            #else:
                #print "    (ignored)"

    def _getTree(self, parent):
        """Return the entire list of cached handles which are children or grandchildren of this handle"""
        
        ## If handle has no children, then there is no need to search for its tree.
        tree = [parent]
        ph = self._getCache(parent)
        prefix = os.path.normcase(os.path.join(parent, ''))
        
        if ph.hasChildren():
            for h in self.cache:
                #print "    tree checking", h[:len(parent)], parent + os.path.sep
                #print "    tree checking:"
                #print "       ", parent
                #print "       ", h
                #if self.cache[h].isGrandchildOf(ph):
                if h[:len(prefix)] == prefix:
                    #print "        hit"
                    tree.append(h)
        return tree

    def _getCache(self, name):
        return self.cache[abspath(name)]
        
    def _setCache(self, name, value):
        self.cache[abspath(name)] = value
        
    def _delCache(self, name):
        del self.cache[abspath(name)]
        
    def _cacheHasName(self, name):
        return abspath(name) in self.cache
        


class FileHandle(QtCore.QObject):
    
    sigChanged = QtCore.Signal(object, object, object)  # (self, change, (args))
    sigDelayedChange = QtCore.Signal(object, object)  # (self, changes)
    
    def __init__(self, path, manager):
        QtCore.QObject.__init__(self)
        self.manager = manager
        self.delayedChanges = []
        self.path = os.path.abspath(path)
        self.parentDir = None
        #self.lock = threading.RLock()
        self.lock = Mutex(QtCore.QMutex.Recursive)
        self.sigproxy = SignalProxy(self.sigChanged, slot=self.delayedChange)
        
    def getFile(self, fn):
        return getFileHandle(os.path.join(self.name(), fn))
        

    def __repr__(self):
        return "<%s '%s' (0x%x)>" % (self.__class__.__name__, self.name(), self.__hash__())

    def __reduce__(self):
        return (getHandle, (self.name(),))

    def name(self, relativeTo=None):
        """Return the full name of this file with its absolute path"""
        #self.checkExists()
        with self.lock:
            path = self.path
            if relativeTo == self:
                path = ''
            elif relativeTo is not None:
                rpath = relativeTo.name()
                if not self.isGrandchildOf(relativeTo):
                    raise Exception("Path %s is not child of %s" % (path, rpath))
                return path[len(os.path.join(rpath, '')):]
            return path
        
    def shortName(self):
        """Return the name of this file without its path"""
        #self.checkExists()
        return os.path.split(self.name())[1]

    def ext(self):
        """Return file's extension"""
        return os.path.splitext(self.name())[1]

    def parent(self):
        self.checkExists()
        with self.lock:
            if self.parentDir is None:
                dirName = os.path.split(self.name())[0]
                self.parentDir = self.manager.getDirHandle(dirName)
            return self.parentDir
        
    def info(self):
        self.checkExists()
        info = self.parent()._fileInfo(self.shortName())
        return advancedTypes.ProtectedDict(info)
        
    def setInfo(self, info=None, **args):
        """Set meta-information for this file. Updates all keys specified in info, leaving others unchanged."""
        if info is None:
            info = args
        self.checkExists()
        self.emitChanged('meta')
        return self.parent()._setFileInfo(self.shortName(), info)
        
    def isManaged(self):
        self.checkExists()
        return self.parent().isManaged(self.shortName())
        
    def move(self, newDir):
        self.checkExists()
        with self.lock:
            oldDir = self.parent()
            fn1 = self.name()
            name = self.shortName()
            fn2 = os.path.join(newDir.name(), name)
            if os.path.exists(fn2):
                raise Exception("Destination file %s already exists." % fn2)
#            print "<> DataManager.move: about to move %s => %s" % (fn1, fn2)

            if oldDir.isManaged() and not newDir.isManaged():
                raise Exception("Not moving managed file to unmanaged location--this would cause loss of meta info.")
            

            os.rename(fn1, fn2)
#            print "<> DataManager.move: moved."
            self.path = fn2
            self.parentDir = None
#            print "<> DataManager.move: inform DM of change.."
            self.manager._handleChanged(self, 'moved', fn1, fn2)
            
            if oldDir.isManaged() and newDir.isManaged():
#                print "<> DataManager.move: new parent index old info.."
                newDir.indexFile(name, info=oldDir._fileInfo(name))
            elif newDir.isManaged():
#                print "<> DataManager.move: new parent index (no info)"
                newDir.indexFile(name)
                
            if oldDir.isManaged() and oldDir.isManaged(name):
#                print "<> DataManager.move: old parent forget child.."
                oldDir.forget(name)
                
#            print "<> DataManager.move: emit 'moved'.."
            self.emitChanged('moved', fn1, fn2)
                
#            print "<> DataManager.move: oldDir emit 'children'"
            oldDir._childChanged()
#            print "<> DataManager.move: newDir emit 'children'"
            newDir._childChanged()
        
    def rename(self, newName):
        #print "Rename %s -> %s" % (self.name(), newName)
        self.checkExists()
        with self.lock:
            parent = self.parent()
            fn1 = self.name()
            oldName = self.shortName()
            fn2 = os.path.join(parent.name(), newName)
            if os.path.exists(fn2):
                raise Exception("Destination file %s already exists." % fn2)
            #print "rename", fn1, fn2
            info = {}
            if parent.isManaged(oldName):
                info = parent._fileInfo(oldName)
                parent.forget(oldName)
            os.rename(fn1, fn2)
            self.path = fn2
            self.manager._handleChanged(self, 'renamed', fn1, fn2)
            if parent.isManaged(oldName):
                parent.indexFile(newName, info=info)
                
            self.emitChanged('renamed', fn1, fn2)
            self.parent()._childChanged()
        
    def delete(self):
        self.checkExists()
        with self.lock:
            parent = self.parent()
            fn1 = self.name()
            oldName = self.shortName()
            if self.isFile():
                os.remove(fn1)
            else:
                shutil.rmtree(fn1)
            self.manager._handleChanged(self, 'deleted', fn1)
            self.path = None
            if parent.isManaged():
                parent.forget(oldName)
            self.emitChanged('deleted', fn1)
            parent._childChanged()
        
    def read(self, *args, **kargs):
        self.checkExists()
        with self.lock:
            typ = self.fileType()
            
            if typ is None:
                fd = open(self.name(), 'r')
                data = fd.read()
                fd.close()
            else:
                cls = filetypes.getFileType(typ)
                data = cls.read(self, *args, **kargs)
                #mod = __import__('lib.filetypes.%s' % typ, fromlist=['*'])
                #func = getattr(mod, 'fromFile')
                #data = func(fileName=self.name())
            
            return data
        
    def fileType(self):
        with self.lock:
            info = self.info()
            
            ## Use the recorded object_type to read the file if possible.
            ## Otherwise, ask the filetypes to choose the type for us.
            if '__object_type__' not in info:
                typ = filetypes.suggestReadType(self)
            else:
                typ = info['__object_type__']
            return typ



    def emitChanged(self, change, *args):
        self.delayedChanges.append(change)
        self.sigChanged.emit(self, change, args)

    def delayedChange(self, args):
        changes = list(set(self.delayedChanges))
        self.delayedChanges = []
        #self.emit(QtCore.SIGNAL('delayedChange'), self, changes)
        self.sigDelayedChange.emit(self, changes)
    
    def hasChildren(self):
        self.checkExists()
        return False
    
    def _parentMoved(self, oldDir, newDir):
        """Inform this object that it has been moved as a result of its (grand)parent having moved."""
        prefix = os.path.join(oldDir, '')
        if self.path[:len(prefix)] != prefix:
            raise Exception("File %s is not in moved tree %s, should not update!" % (self.path, oldDir))
        subName = self.path[len(prefix):]
        #while subName[0] == os.path.sep:
            #subName = subName[1:]
        newName = os.path.join(newDir, subName)
        #print "===", oldDir, newDir, subName, newName
        if not os.path.exists(newName):
            raise Exception("File %s does not exist." % newName)
        self.path = newName
        self.parentDir = None
        #print "parent of %s changed" % self.name()
        self.emitChanged('parent')
        
    def exists(self, name=None):
        if self.path is None:
            return False
        if name is not None:
            raise Exception("Cannot check for subpath existence on FileHandle.")
        return os.path.exists(self.path)

    def checkExists(self):
        if not self.exists():
            raise Exception("File '%s' does not exist." % self.path)

    def checkDeleted(self):
        if self.path is None:
            raise Exception("File has been deleted.")

    def isDir(self, path=None):
        return False
        
    def isFile(self):
        return True
        
    def _deleted(self):
        self.path = None
    
    def isGrandchildOf(self, grandparent):
        """Return true if this files is anywhere in the tree beneath grandparent."""
        gname = os.path.join(grandparent.name(), '')
        return self.name()[:len(gname)] == gname
    
    def write(self, data, **kwargs):
        self.parent().writeFile(data, self.shortName(), **kwargs)
        

    def flushSignals(self):
        """If any delayed signals are pending, send them now."""
        self.sigproxy.flush()


class DirHandle(FileHandle):
    def __init__(self, path, manager, create=False):
        FileHandle.__init__(self, path, manager)
        self._index = None
        self.lsCache = {}  # sortMode: [files...]
        self.cTimeCache = {}
        self._indexFileExists = False
        
        if not os.path.isdir(self.path):
            if create:
                os.mkdir(self.path)
                self.createIndex()
            #else:
            #    raise Exception("Directory %s does not exist." % self.path)
        
        ## Let's avoid reading the index unless we really need to.
        self._indexFileExists = os.path.isfile(self._indexFile())
        #if os.path.isfile(self._indexFile()):
            ### read the index and cache it.
            #self._readIndex()
        #else:
            ### If directory is unmanaged, just leave it that way.
            #pass
        
        
    #def __del__(self):
        #pass
    
    def _indexFile(self):
        """Return the name of the index file for this directory. NOT the same as indexFile()"""
        return os.path.join(self.path, '.index')
    
    def _logFile(self):
        return os.path.join(self.path, '.log')
    
    def __getitem__(self, item):
        #print self.name(), " -> ", item
        #while len(item) > 0 and item[0] == os.path.sep:
            #item = item[1:]
        item = item.lstrip(os.path.sep)
        fileName = os.path.join(self.name(), item)
        return self.manager.getHandle(fileName)
    
    
    def createIndex(self):
        if self.isManaged():
            raise Exception("Directory is already managed!")
        self._writeIndex(OrderedDict([('.', {})]))
        
    def logMsg(self, msg, tags=None):
        """Write a message into the log for this directory."""
        if tags is None:
            tags = {}
        with self.lock:
            if type(tags) is not dict:
                raise Exception("tags argument must be a dict")
            tags['__timestamp__'] = time.time()
            tags['__message__'] = str(msg)
            
            fd = open(self._logFile(), 'a')
            #fcntl.flock(fd, fcntl.LOCK_EX)
            fd.write("%s\n" % repr(tags))
            fd.close()
            self.emitChanged('log', tags)
        
    def readLog(self, recursive=0):
        """Return a list containing one dict for each log line"""
        with self.lock:
            logf = self._logFile()
            if not os.path.exists(logf):
                log = []
            else:
                try:
                    fd = open(logf, 'r')
                    lines = fd.readlines()
                    fd.close()
                    log = map(lambda l: eval(l.strip()), lines)
                except:
                    print "****************** Error reading log file %s! *********************" % logf
                    raise
            
            if recursive > 0:
                for d in self.subDirs():
                    dh = self[d]
                    subLog = dh.readLog(recursive=recursive-1)
                    for msg in subLog:
                        if 'subdir' not in msg:
                            msg['subdir'] = ''
                        msg['subdir'] = os.path.join(dh.shortName(), msg['subdir'])
                    log  = log + subLog
                log.sort(lambda a,b: cmp(a['__timestamp__'], b['__timestamp__']))
            
            return log
        
    def subDirs(self):
        """Return a list of string names for all sub-directories."""
        with self.lock:
            ls = self.ls()
            subdirs = filter(lambda d: os.path.isdir(os.path.join(self.name(), d)), ls)
            return subdirs
    
    def incrementFileName(self, fileName, useExt=True):
        """Given fileName.ext, finds the next available fileName_NNN.ext"""
        #p = Profiler('   increment:')
        files = self.ls()
        #p.mark('ls')
        if useExt:
            (fileName, ext) = os.path.splitext(fileName)
        else:
            ext = ''
        regex = re.compile(fileName + r'_(\d+)')
        files = filter(lambda f: regex.match(f), files)
        #p.mark('filter')
        if len(files) > 0:
            files.sort()
            maxVal = int(regex.match(files[-1]).group(1)) + 1
        else:
            maxVal = 0
        ret = fileName + ('_%03d' % maxVal) + ext
        #p.mark('done')
        #print "incremented name %s to %s" %(fileName, ret)
        return ret
    
    def mkdir(self, name, autoIncrement=False, info=None):
        """Create a new subdirectory, return a new DirHandle object. If autoIncrement is true, add a number to the end of the dir name if it already exists."""
        #prof = Profiler('mkdir')
        if info is None:
            info = {}
        with self.lock:
            #prof.mark('got lock')
            if autoIncrement:
                fullName = self.incrementFileName(name, useExt=False)
            else:
                fullName = name
            #prof.mark('1')
            newDir = os.path.join(self.path, fullName)
            if os.path.isdir(newDir):
                raise Exception("Directory %s already exists." % newDir)
            #prof.mark('2')
            
            ## Create directory
            ndm = self.manager.getDirHandle(newDir, create=True)
            #prof.mark('3')
            t = time.time()
            self._childChanged()
            #prof.mark('4')
            
            if self.isManaged():
                #prof.mark('5')
                ## Mark the creation time in the parent directory so it can sort its full list of files without 
                ## going into each subdir
                self._setFileInfo(fullName, {'__timestamp__': t})
            #prof.mark('6')
            
            ## create the index file in the new directory
            info['__timestamp__'] = t
            ndm.setInfo(info)
            #prof.mark('7')
            self.emitChanged('children', newDir)
            #prof.mark('8')
            return ndm
        
    def getDir(self, subdir, create=False, autoIncrement=False):
        """Return a DirHandle for the specified subdirectory. If the subdir does not exist, it will be created only if create==True"""
        with self.lock:
            ndir = os.path.join(self.path, subdir)
            if not create or os.path.isdir(ndir):
                return self.manager.getDirHandle(ndir)
            else:
                if create:
                    return self.mkdir(subdir, autoIncrement=autoIncrement)
                else:
                    raise Exception('Directory %s does not exist.' % ndir)
        
    def getFile(self, fileName):
        """return a File handle for the named file."""
        fullName = os.path.join(self.name(), fileName)
        #if create:
            #self.createFile(fileName, autoIncrement=autoIncrement, useExt=useExt)
        #if not os.path.isfile(fullName):
        #    raise Exception('File "%s" does not exist.' % fullName)
        fh = self[fileName]
        if not fh.isManaged():
            self.indexFile(fileName)
        return fh
        
    #def createFile(self, fileName, **kwargs):   ## autoIncrement=False, useExt=True, info=None):
        #self.writeFile(None, fileName=fileName, **kwargs)
        
        
    def dirExists(self, dirName):
        return os.path.isdir(os.path.join(self.path, dirName))
            
    def ls(self, normcase=False, sortMode='date', useCache=False):
        """Return a list of all files in the directory.
        If normcase is True, normalize the case of all names in the list.
        sortMode may be 'date', 'alpha', or None."""
        #p = Profiler('      DirHandle.ls:')
        with self.lock:
            #p.mark('lock')
            #self._readIndex()
            #ls = self.index.keys()
            #ls.remove('.')
            if (not useCache) or (sortMode not in self.lsCache):
                self._updateLsCache(sortMode)
                #p.mark('sort')
            files = self.lsCache[sortMode]
            
            if normcase:
                ret = map(os.path.normcase, files)
                #p.mark('return norm')
                return ret
            else:
                ret = files[:]
                #p.mark('return copy')
                return ret
    
    def _updateLsCache(self, sortMode):
        #p.mark('(cache miss)')
        try:
            files = os.listdir(self.name())
        except:
            printExc("Error while listing files in %s:" % self.name())
            files = []
        #p.mark('listdir')
        for i in ['.index', '.log']:
            if i in files:
                files.remove(i)
        #self.lsCache.sort(self._cmpFileTimes)  ## very expensive!
        
        if sortMode == 'date':
            ## Sort files by creation time
            with ProgressDialog("Reading directory data...", maximum=len(files), cancelText=None) as dlg:
                for f in files:
                    if f not in self.cTimeCache:
                        self.cTimeCache[f] = self._getFileCTime(f)
                    dlg += 1
            files.sort(key=lambda f: (self.cTimeCache[f], f))  ## sort by time first, then name.
        elif sortMode == 'alpha':
            ## show directories first when sorting alphabetically.
            files.sort(lambda a,b: 2*cmp(os.path.isdir(os.path.join(self.name(),b)), os.path.isdir(os.path.join(self.name(),a))) + cmp(a,b))
        elif sortMode == None:
            pass
        else:
            raise Exception('Unrecognized sort mode "%s"' % str(sortMode))
            
        self.lsCache[sortMode] = files
    
    def _getFileCTime(self, fileName):
        if self.isManaged():
            index = self._readIndex()
            try:
                t = index[fileName]['__timestamp__']
                return t
            except KeyError:
                pass
            
            ## try getting time directly from file
            try:
                t = self[fileName].info()['__timestamp__']
            except:
                #printExc("No time for file %s:" % fileName)
                #print self[fileName].info().keys()
                pass
                    
        ## if the file has an obvious date in it, use that
        m = re.search(r'(20\d\d\.\d\d?\.\d\d?)', fileName)
        if m is not None:
            return time.mktime(time.strptime(m.groups()[0], "%Y.%m.%d"))
        
        ## if all else fails, just ask the file system
        return os.path.getctime(os.path.join(self.name(), fileName))
    
    #def _cmpFileTimes(self, a, b):
    #    with self.lock:
    #        t1 = t2 = None
    #        if self.isManaged():
    #            index = self._readIndex()
    #            if a in index and '__timestamp__' in index[a]:
    #                t1 = index[a]['__timestamp__']
    #            if b in index and '__timestamp__' in index[b]:
    #                t2 = index[b]['__timestamp__']
    #        if t1 is None:
    #            t1 = os.path.getctime(os.path.join(self.name(), a))
    #        if t2 is None:
    #            t2 = os.path.getctime(os.path.join(self.name(), b))
    #        #print "compare", a, b, t1, t2
    #        return cmp(t1, t2)
    
    def isGrandparentOf(self, child):
        """Return true if child is anywhere in the tree below this directory."""
        return child.isGrandchildOf(self)
    
    def hasChildren(self):
        return len(self.ls()) > 0
    
    def info(self):
        self._readIndex(unmanagedOk=True)  ## returns None if this directory has no index file
        return advancedTypes.ProtectedDict(self._fileInfo('.'))
    
    def _fileInfo(self, file):
        """Return a dict of the meta info stored for file"""
        with self.lock:
            if not self.isManaged():
                return {}
            index = self._readIndex()
            if index.has_key(file):
                return index[file]
            else:
                return {}
                #raise Exception("File %s is not indexed" % file)
    
    def isDir(self, path=None):
        with self.lock:
            if path is None:
                return True
            else:
                return self[path].isDir()
        
    def isFile(self, fileName=None):
        if fileName is None:
            return False
        with self.lock:
            fn = os.path.abspath(os.path.join(self.path, fileName))
            return os.path.isfile(fn)
        
    def createFile(self, fileName, info=None, autoIncrement=False):
        """Create a blank file"""
        if info is None:
            info = {}   ## never put {} in the function default
        
        t = time.time()
        with self.lock:
            ## Increment file name
            if autoIncrement:
                fileName = self.incrementFileName(fileName)
            
            ## Write file
            open(os.path.join(self.name(), fileName), 'w')
            
            self._childChanged()
            
            ## Write meta-info
            if not info.has_key('__timestamp__'):
                info['__timestamp__'] = t
            self._setFileInfo(fileName, info)
            self.emitChanged('children', fileName)
            return self[fileName]
        
    def writeFile(self, obj, fileName, info=None, autoIncrement=False, fileType=None, **kwargs):
        """Write a file to this directory using obj.write(fileName), store info in the index.
        Will try to convert obj into a FileType if the correct type exists.
        """
        #print "Write file", fileName
        #p = Profiler('  ' + fileName + ': ')
        
        if info is None:
            info = {}   ## never put {} in the function default
        else:
            info = info.copy()  ## we modify this later; need to copy first
        
        t = time.time()
        with self.lock:
            #p.mark('lock')
                    
            if fileType is None:
                fileType = filetypes.suggestWriteType(obj, fileName)
                
            if fileType is None:
                raise Exception("Can not create file from object of type %s" % str(type(obj)))

            #p.mark('type')
                
            fileClass = filetypes.getFileType(fileType)
            #p.mark('get class')

            ## Increment file name
            if autoIncrement:
                fileName = self.incrementFileName(fileName)
            
            #p.mark('increment')
            ## Write file
            fileName = fileClass.write(obj, self, fileName, **kwargs)
            
            #p.mark('write')
            self._childChanged()
            #p.mark('update')
            ## Write meta-info
            if not info.has_key('__object_type__'):
                info['__object_type__'] = fileType
            if not info.has_key('__timestamp__'):
                info['__timestamp__'] = t
            self._setFileInfo(fileName, info)
            #p.mark('meta')
            self.emitChanged('children', fileName)
            #p.mark('emit')
            return self[fileName]
    
    def indexFile(self, fileName, info=None, protect=False):
        """Add a pre-existing file into the index. Overwrites any pre-existing info for the file unless protect is True"""
        #print "DirHandle: Adding file %s to index" % fileName
        if info is None:
            info = {}
        with self.lock:
            if not self.isManaged():
                self.createIndex()
            index = self._readIndex()
            fn = os.path.join(self.path, fileName)
            if not (os.path.isfile(fn) or os.path.isdir(fn)):
                raise Exception("File %s does not exist." % fn)
                
            #append = True
            if fileName in index:
                #append = False
                if protect:
                    raise Exception("File %s is already indexed." % fileName)

            self._setFileInfo(fileName, info)
            #self.emitChanged('children', fileName)
            self.emitChanged('meta', fileName)
    
    def forget(self, fileName):
        """Remove fileName from the index for this directory"""
        #print "DirHandle: forget", fileName
        with self.lock:
            if not self.isManaged(fileName):
                raise Exception("Can not forget %s, not managed" % fileName)
            index = self._readIndex(lock=False)
            if fileName in index:
                try:
                    #index.remove(fileName)
                    del index[fileName]
                except:
                    print type(index)
                    raise
                self._writeIndex(index, lock=False)
                #self.emitChanged('children', fileName)
                self.emitChanged('meta', fileName)
        
    def isManaged(self, fileName=None):
        with self.lock:
            if self._indexFileExists is False:
                return False
            if fileName is None:
                return True
            else:
                ind = self._readIndex(unmanagedOk=True)
                if ind is None:
                    return False
                return (fileName in ind)

    
    def setInfo(self, *args, **kargs):
        self._setFileInfo('.', *args, **kargs)
        
        
        
        
    #def parent(self):
        #with self.lock:
            #pdir = os.path.normpath(os.path.join(self.path, '..'))
            #return self.manager.getDirHandle(pdir)

        

    def exists(self, name=None):
        """Returns True if the file 'name' exists in this directory, False otherwise."""
        with self.lock:
            if self.path is None:
                return False
            if name is None:
                return os.path.exists(self.path)
            
            try:
                fn = os.path.abspath(os.path.join(self.path, name))
            except:
                print self.path, name
                raise
            return os.path.exists(fn)

    def _setFileInfo(self, fileName, info=None, **args):
        """Set or update meta-information array for fileName. If merge is false, the info dict is completely overwritten."""
        if info is None:
            info = args
        #prof = Profiler('setFileInfo')
        with self.lock:
            #prof.mark('1')
            if not self.isManaged():
                self.createIndex()
            #prof.mark('2')
            index = self._readIndex(lock=False)
            #prof.mark('3')
            append = False
            if fileName not in index:
                index[fileName] = {}
                append = True
            #prof.mark('4')
                
            for k in info:
                index[fileName][k] = info[k]
            #prof.mark('5')
                
            if append:
                self._appendIndex({fileName: info})
                
                #prof.mark('6')
            else:
                self._writeIndex(index, lock=False)
                #prof.mark('6a')
            self.emitChanged('meta', fileName)
            #prof.mark('7')
            #prof.finish()
        
    def _readIndex(self, lock=True, unmanagedOk=False):
        with self.lock:
            indexFile = self._indexFile()
            if self._index is None or os.path.getmtime(indexFile) != self._indexMTime:
                if not os.path.isfile(indexFile):
                    if unmanagedOk:
                        return None
                    else:
                        raise Exception("Directory '%s' is not managed!" % (self.name()))
                try:
                    self._index = readConfigFile(indexFile)
                    self._indexMTime = os.path.getmtime(indexFile)
                    #self.checkIndex()  ## This is a bad idea. Lots of ways this can lead to data loss.
                except:
                    print "***************Error while reading index file %s!*******************" % indexFile
                    raise
                #print "fresh index:", type(self._index)
            #else:
                #print "cached index:", type(self._index)
            return self._index
        
    def _writeIndex(self, newIndex, lock=True):
        #print "write index:", self
        #import traceback
        #traceback.print_stack()
        with self.lock:
            
            writeConfigFile(newIndex, self._indexFile())
            #print "Write", type(newIndex)
            self._index = newIndex
            self._indexMTime = os.path.getmtime(self._indexFile())
            self._indexFileExists = True

    def _appendIndex(self, info):
        with self.lock:
            indexFile = self._indexFile()
            appendConfigFile(info, indexFile)
            self._indexFileExists = True
            for k in info:
                self._index[k] = info[k]
            self._indexMTime = os.path.getmtime(indexFile)
        
    def checkIndex(self):
        ind = self._readIndex(unmanagedOk=True)
        if ind is None:
            return
        changed = False
        for f in ind:
            if not self.exists(f):
                print "File %s is no more, removing from index." % (os.path.join(self.name(), f))
                del ind[f]
                #ind.remove(f)
                changed = True
        if changed:
            self._writeIndex(ind)
            
        
    def _childChanged(self):
        self.lsCache = {}
        self.emitChanged('children')


dm = DataManager()

