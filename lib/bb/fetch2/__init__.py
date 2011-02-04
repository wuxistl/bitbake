# ex:ts=4:sw=4:sts=4:et
# -*- tab-width: 4; c-basic-offset: 4; indent-tabs-mode: nil -*-
"""
BitBake 'Fetch' implementations

Classes for obtaining upstream sources for the
BitBake build tools.
"""

# Copyright (C) 2003, 2004  Chris Larson
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Based on functions from the base bb module, Copyright 2003 Holger Schurig

from __future__ import absolute_import
from __future__ import print_function
import os, re
import logging
import bb
from   bb import data
from   bb import persist_data
from   bb import utils

__version__ = "2"

logger = logging.getLogger("BitBake.Fetch")

class BBFetchException(Exception):
    """Class all fetch exceptions inherit from"""
    def __init__(self, message):
         self.msg = message
         Exception.__init__(self, message)

    def __str__(self):
         return self.msg

class MalformedUrl(BBFetchException):
    """Exception raised when encountering an invalid url"""
    def __init__(self, url):
         self.msg = "The URL: '%s' is invalid and cannot be interpreted" % url
         self.url = url
         Exception.__init__(self, self.msg)

class FetchError(BBFetchException):
    """General fetcher exception when something happens incorrectly"""
    def __init__(self, message, url = None):
         self.msg = "Fetcher failure for URL: '%s'. %s" % (url, message)
         self.url = url
         Exception.__init__(self, self.msg)

class UnpackError(BBFetchException):
    """General fetcher exception when something happens incorrectly when unpacking"""
    def __init__(self, message, url):
         self.msg = "Unpack failure for URL: '%s'. %s" % (url, message)
         self.url = url
         Exception.__init__(self, self.msg)

class NoMethodError(BBFetchException):
    """Exception raised when there is no method to obtain a supplied url or set of urls"""
    def __init__(self, url):
         self.msg = "Could not find a fetcher which supports the URL: '%s'" % url
         self.url = url
         Exception.__init__(self, self.msg)

class MissingParameterError(BBFetchException):
    """Exception raised when a fetch method is missing a critical parameter in the url"""
    def __init__(self, missing, url):
         self.msg = "URL: '%s' is missing the required parameter '%s'" % (url, missing)
         self.url = url
         self.missing = missing
         Exception.__init__(self, self.msg)

class ParameterError(BBFetchException):
    """Exception raised when a url cannot be proccessed due to invalid parameters."""
    def __init__(self, message, url):
         self.msg = "URL: '%s' has invalid parameters. %s" % (url, message)
         self.url = url
         Exception.__init__(self, self.msg)

class MD5SumError(BBFetchException):
    """Exception raised when a MD5 checksum of a file does not match for a downloaded file"""
    def __init__(self, path, wanted, got, url):
         self.msg = "File: '%s' has md5 sum %s when %s was expected (from URL: '%s')" % (path, got, wanted, url)
         self.url = url
         self.path = path
         self.wanted = wanted
         self.got = got
         Exception.__init__(self, self.msg)

class SHA256SumError(MD5SumError):
    """Exception raised when a SHA256 checksum of a file does not match for a downloaded file"""

def decodeurl(url):
    """Decodes an URL into the tokens (scheme, network location, path,
    user, password, parameters).
    """

    m = re.compile('(?P<type>[^:]*)://((?P<user>.+)@)?(?P<location>[^;]+)(;(?P<parm>.*))?').match(url)
    if not m:
        raise MalformedUrl(url)

    type = m.group('type')
    location = m.group('location')
    if not location:
        raise MalformedUrl(url)
    user = m.group('user')
    parm = m.group('parm')

    locidx = location.find('/')
    if locidx != -1 and type.lower() != 'file':
        host = location[:locidx]
        path = location[locidx:]
    else:
        host = ""
        path = location
    if user:
        m = re.compile('(?P<user>[^:]+)(:?(?P<pswd>.*))').match(user)
        if m:
            user = m.group('user')
            pswd = m.group('pswd')
    else:
        user = ''
        pswd = ''

    p = {}
    if parm:
        for s in parm.split(';'):
            s1, s2 = s.split('=')
            p[s1] = s2

    return (type, host, path, user, pswd, p)

def encodeurl(decoded):
    """Encodes a URL from tokens (scheme, network location, path,
    user, password, parameters).
    """

    (type, host, path, user, pswd, p) = decoded

    if not path:
        raise MissingParameterError('path', "encoded from the data %s" % str(decoded))
    if not type:
        raise MissingParameterError('type', "encoded from the data %s" % str(decoded))
    url = '%s://' % type
    if user and type != "file":
        url += "%s" % user
        if pswd:
            url += ":%s" % pswd
        url += "@"
    if host and type != "file":
        url += "%s" % host
    url += "%s" % path
    if p:
        for parm in p:
            url += ";%s=%s" % (parm, p[parm])

    return url

def uri_replace(uri, uri_find, uri_replace, d):
    if not uri or not uri_find or not uri_replace:
        logger.debug(1, "uri_replace: passed an undefined value, not replacing")
    uri_decoded = list(decodeurl(uri))
    uri_find_decoded = list(decodeurl(uri_find))
    uri_replace_decoded = list(decodeurl(uri_replace))
    result_decoded = ['', '', '', '', '', {}]
    for i in uri_find_decoded:
        loc = uri_find_decoded.index(i)
        result_decoded[loc] = uri_decoded[loc]
        if isinstance(i, basestring):
            if (re.match(i, uri_decoded[loc])):
                result_decoded[loc] = re.sub(i, uri_replace_decoded[loc], uri_decoded[loc])
                if uri_find_decoded.index(i) == 2:
                    if d:
                        localfn = bb.fetch2.localpath(uri, d)
                        if localfn:
                            result_decoded[loc] = os.path.join(os.path.dirname(result_decoded[loc]), os.path.basename(bb.fetch2.localpath(uri, d)))
            else:
                return uri
    return encodeurl(result_decoded)

methods = []
urldata_cache = {}
saved_headrevs = {}

def fetcher_init(d):
    """
    Called to initialize the fetchers once the configuration data is known.
    Calls before this must not hit the cache.
    """
    pd = persist_data.persist(d)
    # When to drop SCM head revisions controlled by user policy
    srcrev_policy = bb.data.getVar('BB_SRCREV_POLICY', d, True) or "clear"
    if srcrev_policy == "cache":
        logger.debug(1, "Keeping SRCREV cache due to cache policy of: %s", srcrev_policy)
    elif srcrev_policy == "clear":
        logger.debug(1, "Clearing SRCREV cache due to cache policy of: %s", srcrev_policy)
        try:
            bb.fetch2.saved_headrevs = pd['BB_URI_HEADREVS'].items()
        except:
            pass
        del pd['BB_URI_HEADREVS']
    else:
        raise FetchError("Invalid SRCREV cache policy of: %s" % srcrev_policy)

    for m in methods:
        if hasattr(m, "init"):
            m.init(d)

def fetcher_compare_revisions(d):
    """
    Compare the revisions in the persistant cache with current values and
    return true/false on whether they've changed.
    """

    pd = persist_data.persist(d)
    data = pd['BB_URI_HEADREVS'].items()
    data2 = bb.fetch2.saved_headrevs

    changed = False
    for key in data:
        if key not in data2 or data2[key] != data[key]:
            logger.debug(1, "%s changed", key)
            changed = True
            return True
        else:
            logger.debug(2, "%s did not change", key)
    return False

def mirror_from_string(data):
    return [ i.split() for i in (data or "").replace('\\n','\n').split('\n') if i ]

def verify_checksum(u, ud, d):
    """
    verify the MD5 and SHA256 checksum for downloaded src

    return value:
        - True: checksum matched
        - False: checksum unmatched

    if checksum is missing in recipes file, "BB_STRICT_CHECKSUM" decide the return value.
    if BB_STRICT_CHECKSUM = "1" then return false as unmatched, otherwise return true as
    matched
    """

    if not ud.type in ["http", "https", "ftp", "ftps"]:
        return

    md5data = bb.utils.md5_file(ud.localpath)
    sha256data = bb.utils.sha256_file(ud.localpath)

    if (ud.md5_expected == None or ud.sha256_expected == None):
        logger.warn('Missing SRC_URI checksum for %s, consider adding to the recipe:\n'
                    'SRC_URI[%s] = "%s"\nSRC_URI[%s] = "%s"',
                    ud.localpath, ud.md5_name, md5data,
                    ud.sha256_name, sha256data)
        if bb.data.getVar("BB_STRICT_CHECKSUM", d, True) == "1":
            raise FetchError("No checksum specified for %s." % u, u)
        return

    if ud.md5_expected != md5data:
        raise MD5SumError(ud.localpath, ud.md5_expected, md5data, u)

    if ud.sha256_expected != sha256data:
        raise SHA256SumError(ud.localpath, ud.sha256_expected, sha256data, u)

def subprocess_setup():
    import signal
    # Python installs a SIGPIPE handler by default. This is usually not what
    # non-Python subprocesses expect.
    # SIGPIPE errors are known issues with gzip/bash
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

def download_update(result, target):
    if os.path.exists(target):
        return
    if not result or not os.path.exists(result):
        return
    if target != result:
        os.symlink(result, target)
    return

def get_autorev(d):
    #  only not cache src rev in autorev case
    if bb.data.getVar('BB_SRCREV_POLICY', d, True) != "cache":
        bb.data.setVar('__BB_DONT_CACHE', '1', d)
    return "AUTOINC"

def get_srcrev(d):
    """
    Return the version string for the current package
    (usually to be used as PV)
    Most packages usually only have one SCM so we just pass on the call.
    In the multi SCM case, we build a value based on SRCREV_FORMAT which must
    have been set.
    """

    scms = []
    fetcher = Fetch(bb.data.getVar('SRC_URI', d, True).split(), d)
    urldata = fetcher.ud
    for u in urldata:
        if urldata[u].method.supports_srcrev():
            scms.append(u)

    if len(scms) == 0:
        raise FetchError("SRCREV was used yet no valid SCM was found in SRC_URI")

    if len(scms) == 1 and len(urldata[scms[0]].names) == 1:
        return urldata[scms[0]].method.sortable_revision(scms[0], urldata[scms[0]], d, urldata[scms[0]].names[0])

    #
    # Mutiple SCMs are in SRC_URI so we resort to SRCREV_FORMAT
    #
    format = bb.data.getVar('SRCREV_FORMAT', d, True)
    if not format:
        raise FetchError("The SRCREV_FORMAT variable must be set when multiple SCMs are used.")

    for scm in scms:
        ud = urldata[scm]
        for name in ud.names:
            rev = ud.method.sortable_revision(scm, ud, d, name)
            format = format.replace(name, rev)

    return format

def localpath(url, d):
    fetcher = bb.fetch2.Fetch([url], d)
	return fetcher.localpath(url)

def runfetchcmd(cmd, d, quiet = False, cleanup = []):
    """
    Run cmd returning the command output
    Raise an error if interrupted or cmd fails
    Optionally echo command output to stdout
    Optionally remove the files/directories listed in cleanup upon failure
    """

    # Need to export PATH as binary could be in metadata paths
    # rather than host provided
    # Also include some other variables.
    # FIXME: Should really include all export varaiables?
    exportvars = ['PATH', 'GIT_PROXY_COMMAND', 'GIT_PROXY_HOST',
                  'GIT_PROXY_PORT', 'GIT_CONFIG', 'http_proxy', 'ftp_proxy',
                  'https_proxy', 'no_proxy', 'ALL_PROXY', 'all_proxy',
                  'SSH_AUTH_SOCK', 'SSH_AGENT_PID', 'HOME']

    for var in exportvars:
        val = data.getVar(var, d, True)
        if val:
            cmd = 'export ' + var + '=\"%s\"; %s' % (val, cmd)

    logger.debug(1, "Running %s", cmd)

    # redirect stderr to stdout
    stdout_handle = os.popen(cmd + " 2>&1", "r")
    output = ""

    while True:
        line = stdout_handle.readline()
        if not line:
            break
        if not quiet:
            print(line, end=' ')
        output += line

    status = stdout_handle.close() or 0
    signal = status >> 8
    exitstatus = status & 0xff

    if (signal or status != 0):
        for f in cleanup:
            try:
                bb.utils.remove(f, True)
            except OSError:
                pass

        if signal:
            raise FetchError("Fetch command %s failed with signal %s, output:\n%s" % (cmd, signal, output))
        elif status != 0:
            raise FetchError("Fetch command %s failed with exit code %s, output:\n%s" % (cmd, status, output))

    return output

def check_network_access(d, info = ""):
    """
    log remote network access, and error if BB_NO_NETWORK is set
    """
    if bb.data.getVar("BB_NO_NETWORK", d, True) == "1":
        raise FetchError("BB_NO_NETWORK is set, but the fetcher code attempted network access with the command %s" % info)
    else:
        logger.debug(1, "Fetcher accessed the network with the command %s" % info)

def try_mirrors(d, uri, mirrors, check = False, force = False):
    """
    Try to use a mirrored version of the sources.
    This method will be automatically called before the fetchers go.

    d Is a bb.data instance
    uri is the original uri we're trying to download
    mirrors is the list of mirrors we're going to try
    """
    fpath = os.path.join(data.getVar("DL_DIR", d, True), os.path.basename(uri))
    if not check and os.access(fpath, os.R_OK) and not force:
        logger.debug(1, "%s already exists, skipping checkout.", fpath)
        return fpath

    ld = d.createCopy()
    for (find, replace) in mirrors:
        newuri = uri_replace(uri, find, replace, ld)
        if newuri != uri:
            try:
                ud = FetchData(newuri, ld)
            except bb.fetch2.NoMethodError:
                logger.debug(1, "No method for %s", uri)
                continue

            ud.setup_localpath(ld)

            try:
                if check:
                    found = ud.method.checkstatus(newuri, ud, ld)
                    if found:
                        return found
                else:
                    ud.method.download(newuri, ud, ld)
                    if hasattr(ud.method,"build_mirror_data"):
                        ud.method.build_mirror_data(newuri, ud, ld)
                    return ud.localpath
            except (bb.fetch2.MissingParameterError,
                    bb.fetch2.FetchError,
                    bb.fetch2.MD5SumError):
                import sys
                (type, value, traceback) = sys.exc_info()
                logger.debug(2, "Mirror fetch failure: %s", value)
                bb.utils.remove(ud.localpath)
                continue
    return None

def srcrev_internal_helper(ud, d, name):
    """
    Return:
        a) a source revision if specified
        b) latest revision if SRCREV="AUTOINC"
        c) None if not specified
    """

    if 'rev' in ud.parm:
        return ud.parm['rev']

    if 'tag' in ud.parm:
        return ud.parm['tag']

    rev = None
    if name != '':
        pn = data.getVar("PN", d, True)
        rev = data.getVar("SRCREV_%s_pn-%s" % (name, pn), d, True)
        if not rev:
            rev = data.getVar("SRCREV_%s" % name, d, True)
    if not rev:
        rev = data.getVar("SRCREV", d, True)
    if rev == "INVALID":
        raise FetchError("Please set SRCREV to a valid value", ud.url)
    if rev == "AUTOINC":
        rev = ud.method.latest_revision(ud.url, ud, d, name)

    return rev

class FetchData(object):
    """
    A class which represents the fetcher state for a given URI.
    """
    def __init__(self, url, d):
        self.localfile = ""
        self.localpath = None
        self.lockfile = None
        (self.type, self.host, self.path, self.user, self.pswd, self.parm) = decodeurl(data.expand(url, d))
        self.date = self.getSRCDate(d)
        self.url = url
        if not self.user and "user" in self.parm:
            self.user = self.parm["user"]
        if not self.pswd and "pswd" in self.parm:
            self.pswd = self.parm["pswd"]
        self.setup = False

        if "name" in self.parm:
            self.md5_name = "%s.md5sum" % self.parm["name"]
            self.sha256_name = "%s.sha256sum" % self.parm["name"]
        else:
            self.md5_name = "md5sum"
            self.sha256_name = "sha256sum"
        self.md5_expected = bb.data.getVarFlag("SRC_URI", self.md5_name, d)
        self.sha256_expected = bb.data.getVarFlag("SRC_URI", self.sha256_name, d)

        self.names = self.parm.get("name",'default').split(',')

        self.method = None
        for m in methods:
            if m.supports(url, self, d):
                self.method = m
                break                

        if not self.method:
            raise NoMethodError(url)

        if self.method.supports_srcrev():
            self.revisions = {}
            for name in self.names:
                self.revisions[name] = srcrev_internal_helper(self, d, name)

            # add compatibility code for non name specified case
            if len(self.names) == 1:
                self.revision = self.revisions[self.names[0]]

        if hasattr(self.method, "urldata_init"):
            self.method.urldata_init(self, d)

        if "localpath" in self.parm:
            # if user sets localpath for file, use it instead.
            self.localpath = self.parm["localpath"]
            self.basename = os.path.basename(self.localpath)
        elif self.localfile:
            self.localpath = self.method.localpath(self.url, self, d)

        if self.localfile and self.localpath:
            # Note: These files should always be in DL_DIR whereas localpath may not be.
            basepath = bb.data.expand("${DL_DIR}/%s" % os.path.basename(self.localpath), d)
            self.donestamp = basepath + '.done'
            self.lockfile = basepath + '.lock'

    def setup_localpath(self, d):
        if not self.localpath:
            self.localpath = self.method.localpath(self.url, self, d)

    def getSRCDate(self, d):
        """
        Return the SRC Date for the component

        d the bb.data module
        """
        if "srcdate" in self.parm:
            return self.parm['srcdate']

        pn = data.getVar("PN", d, True)

        if pn:
            return data.getVar("SRCDATE_%s" % pn, d, True) or data.getVar("SRCDATE", d, True) or data.getVar("DATE", d, True)

        return data.getVar("SRCDATE", d, True) or data.getVar("DATE", d, True)

class FetchMethod(object):
    """Base class for 'fetch'ing data"""

    def __init__(self, urls = []):
        self.urls = []

    def supports(self, url, urldata, d):
        """
        Check to see if this fetch class supports a given url.
        """
        return 0

    def localpath(self, url, urldata, d):
        """
        Return the local filename of a given url assuming a successful fetch.
        Can also setup variables in urldata for use in go (saving code duplication
        and duplicate code execution)
        """
        return os.path.join(data.getVar("DL_DIR", d, True), urldata.localfile)

    def _strip_leading_slashes(self, relpath):
        """
        Remove leading slash as os.path.join can't cope
        """
        while os.path.isabs(relpath):
            relpath = relpath[1:]
        return relpath

    def setUrls(self, urls):
        self.__urls = urls

    def getUrls(self):
        return self.__urls

    urls = property(getUrls, setUrls, None, "Urls property")

    def forcefetch(self, url, urldata, d):
        """
        Force a fetch, even if localpath exists?
        """
        return False

    def supports_srcrev(self):
        """
        The fetcher supports auto source revisions (SRCREV)
        """
        return False

    def download(self, url, urldata, d):
        """
        Fetch urls
        Assumes localpath was called first
        """
        raise NoMethodError(url)

    def unpack(self, urldata, rootdir, data):
        import subprocess
        file = urldata.localpath
        dots = file.split(".")
        if dots[-1] in ['gz', 'bz2', 'Z']:
            efile = os.path.join(bb.data.getVar('WORKDIR', data, True),os.path.basename('.'.join(dots[0:-1])))
        else:
            efile = file
        cmd = None
        if file.endswith('.tar'):
            cmd = 'tar x --no-same-owner -f %s' % file
        elif file.endswith('.tgz') or file.endswith('.tar.gz') or file.endswith('.tar.Z'):
            cmd = 'tar xz --no-same-owner -f %s' % file
        elif file.endswith('.tbz') or file.endswith('.tbz2') or file.endswith('.tar.bz2'):
            cmd = 'bzip2 -dc %s | tar x --no-same-owner -f -' % file
        elif file.endswith('.gz') or file.endswith('.Z') or file.endswith('.z'):
            cmd = 'gzip -dc %s > %s' % (file, efile)
        elif file.endswith('.bz2'):
            cmd = 'bzip2 -dc %s > %s' % (file, efile)
        elif file.endswith('.tar.xz'):
            cmd = 'xz -dc %s | tar x --no-same-owner -f -' % file
        elif file.endswith('.xz'):
            cmd = 'xz -dc %s > %s' % (file, efile)
        elif file.endswith('.zip') or file.endswith('.jar'):
            cmd = 'unzip -q -o'
            if 'dos' in urldata.parm:
                cmd = '%s -a' % cmd
            cmd = "%s '%s'" % (cmd, file)
        elif os.path.isdir(file):
            filesdir = os.path.realpath(bb.data.getVar("FILESDIR", data, True))
            destdir = "."
            if file[0:len(filesdir)] == filesdir:
                destdir = file[len(filesdir):file.rfind('/')]
                destdir = destdir.strip('/')
                if len(destdir) < 1:
                    destdir = "."
                elif not os.access("%s/%s" % (rootdir, destdir), os.F_OK):
                    os.makedirs("%s/%s" % (rootdir, destdir))
            cmd = 'cp -pPR %s %s/%s/' % (file, rootdir, destdir)
        else:
            if not 'patch' in urldata.parm:
                # The "destdir" handling was specifically done for FILESPATH
                # items.  So, only do so for file:// entries.
                if urldata.type == "file" and urldata.path.find("/") != -1:
                    destdir = urldata.path.rsplit("/", 1)[0]
                else:
                    destdir = "."
                bb.mkdirhier("%s/%s" % (rootdir, destdir))
                cmd = 'cp %s %s/%s/' % (file, rootdir, destdir)

        if not cmd:
            return

        dest = os.path.join(rootdir, os.path.basename(file))
        if os.path.exists(dest):
            if os.path.samefile(file, dest):
                return

        # Change to subdir before executing command
        save_cwd = os.getcwd();
        os.chdir(rootdir)
        if 'subdir' in urldata.parm:
            newdir = ("%s/%s" % (rootdir, urldata.parm['subdir']))
            bb.mkdirhier(newdir)
            os.chdir(newdir)

        cmd = "PATH=\"%s\" %s" % (bb.data.getVar('PATH', data, True), cmd)
        bb.note("Unpacking %s to %s/" % (file, os.getcwd()))
        ret = subprocess.call(cmd, preexec_fn=subprocess_setup, shell=True)

        os.chdir(save_cwd)

        if ret != 0:
            raise UnpackError("Unpack command %s failed with return value %s" % (cmd, ret), urldata.url)

        return

    def try_premirror(self, url, urldata, d):
        """
        Should premirrors be used?
        """
        if urldata.method.forcefetch(url, urldata, d):
            return True
        elif os.path.exists(urldata.donestamp) and os.path.exists(urldata.localfile):
            return False
        else:
            return True

    def checkstatus(self, url, urldata, d):
        """
        Check the status of a URL
        Assumes localpath was called first
        """
        logger.info("URL %s could not be checked for status since no method exists.", url)
        return True

    def localcount_internal_helper(ud, d, name):
        """
        Return:
            a) a locked localcount if specified
            b) None otherwise
        """

        localcount = None
        if name != '':
            pn = data.getVar("PN", d, True)
            localcount = data.getVar("LOCALCOUNT_" + name, d, True)
        if not localcount:
            localcount = data.getVar("LOCALCOUNT", d, True)
        return localcount

    localcount_internal_helper = staticmethod(localcount_internal_helper)

    def latest_revision(self, url, ud, d, name):
        """
        Look in the cache for the latest revision, if not present ask the SCM.
        """
        if not hasattr(self, "_latest_revision"):
            raise ParameterError("The fetcher for this URL does not support _latest_revision", url)

        pd = persist_data.persist(d)
        revs = pd['BB_URI_HEADREVS']
        key = self.generate_revision_key(url, ud, d, name)
        rev = revs[key]
        if rev != None:
            return str(rev)

        revs[key] = rev = self._latest_revision(url, ud, d, name)
        return rev

    def sortable_revision(self, url, ud, d, name):
        """

        """
        if hasattr(self, "_sortable_revision"):
            return self._sortable_revision(url, ud, d)

        pd = persist_data.persist(d)
        localcounts = pd['BB_URI_LOCALCOUNT']
        key = self.generate_revision_key(url, ud, d, name)

        latest_rev = self._build_revision(url, ud, d, name)
        last_rev = localcounts[key + '_rev']
        uselocalcount = bb.data.getVar("BB_LOCALCOUNT_OVERRIDE", d, True) or False
        count = None
        if uselocalcount:
            count = FetchMethod.localcount_internal_helper(ud, d, name)
        if count is None:
            count = localcounts[key + '_count'] or "0"

        if last_rev == latest_rev:
            return str(count + "+" + latest_rev)

        buildindex_provided = hasattr(self, "_sortable_buildindex")
        if buildindex_provided:
            count = self._sortable_buildindex(url, ud, d, latest_rev)

        if count is None:
            count = "0"
        elif uselocalcount or buildindex_provided:
            count = str(count)
        else:
            count = str(int(count) + 1)

        localcounts[key + '_rev'] = latest_rev
        localcounts[key + '_count'] = count

        return str(count + "+" + latest_rev)

    def generate_revision_key(self, url, ud, d, name):
        key = self._revision_key(url, ud, d, name)
        return "%s-%s" % (key, bb.data.getVar("PN", d, True) or "")

class Fetch(object):
    def __init__(self, urls, d):
        if len(urls) == 0:
            urls = d.getVar("SRC_URI", True).split()
        self.urls = urls
        self.d = d
        self.ud = {}

        fn = bb.data.getVar('FILE', d, True)
        if fn in urldata_cache:
            self.ud = urldata_cache[fn]

        for url in urls:
            if url not in self.ud:
                self.ud[url] = FetchData(url, d)

        urldata_cache[fn] = self.ud

    def localpath(self, url):
        if url not in self.urls:
            self.ud[url] = FetchData(url, self.d)

        self.ud[url].setup_localpath(self.d)
        return bb.data.expand(self.ud[url].localpath, self.d)

    def localpaths(self):
        """
        Return a list of the local filenames, assuming successful fetch
        """
        local = []

        for u in self.urls:
            ud = self.ud[u]
            ud.setup_localpath(self.d)
            local.append(ud.localpath)

        return local

    def download(self, urls = []):
        """
        Fetch all urls
        """
        if len(urls) == 0:
            urls = self.urls

        for u in urls:
            ud = self.ud[u]
            ud.setup_localpath(self.d)
            m = ud.method
            localpath = ""

            if not ud.localfile:
                continue

            lf = bb.utils.lockfile(ud.lockfile)

            if m.try_premirror(u, ud, self.d):
                # First try fetching uri, u, from PREMIRRORS
                mirrors = mirror_from_string(bb.data.getVar('PREMIRRORS', self.d, True))
                localpath = try_mirrors(self.d, u, mirrors, False, m.forcefetch(u, ud, self.d))
            elif os.path.exists(ud.localfile):
                localpath = ud.localfile

            download_update(localpath, ud.localpath)

            # Need to re-test forcefetch() which will return true if our copy is too old
            if m.forcefetch(u, ud, self.d) or not localpath:
                # Next try fetching from the original uri, u
                try:
                    m.download(u, ud, self.d)
                    if hasattr(m, "build_mirror_data"):
                        m.build_mirror_data(u, ud, self.d)
                    localpath = ud.localpath
                    download_update(localpath, ud.localpath)

                except FetchError:
                    # Remove any incomplete file
                    bb.utils.remove(ud.localpath)
                    # Finally, try fetching uri, u, from MIRRORS
                    mirrors = mirror_from_string(bb.data.getVar('MIRRORS', self.d, True))
                    localpath = try_mirrors (self.d, u, mirrors)

            if not localpath or not os.path.exists(localpath):
                raise FetchError("Unable to fetch URL %s from any source." % u, u)

            download_update(localpath, ud.localpath)

            if os.path.exists(ud.donestamp):
                # Touch the done stamp file to show active use of the download
                try:
                    os.utime(ud.donestamp, None)
                except:
                    # Errors aren't fatal here
                    pass
            else:
                # Only check the checksums if we've not seen this item before, then create the stamp
                verify_checksum(u, ud, self.d)
                open(ud.donestamp, 'w').close()

            bb.utils.unlockfile(lf)

    def checkstatus(self, urls = []):
        """
        Check all urls exist upstream
        """

        if len(urls) == 0:
            urls = self.urls

        for u in urls:
            ud = self.ud[u]
            ud.setup_localpath(self.d)
            m = ud.method
            logger.debug(1, "Testing URL %s", u)
            # First try checking uri, u, from PREMIRRORS
            mirrors = mirror_from_string(bb.data.getVar('PREMIRRORS', self.d, True))
            ret = try_mirrors(self.d, u, mirrors, True)
            if not ret:
                # Next try checking from the original uri, u
                try:
                    ret = m.checkstatus(u, ud, self.d)
                except:
                    # Finally, try checking uri, u, from MIRRORS
                    mirrors = mirror_from_string(bb.data.getVar('MIRRORS', self.d, True))
                    ret = try_mirrors (self.d, u, mirrors, True)

            if not ret:
                raise FetchError("URL %s doesn't work" % u, u)

    def unpack(self, root, urls = []):
        """
        Check all urls exist upstream
        """

        if len(urls) == 0:
            urls = self.urls

        for u in urls:
            ud = self.ud[u]
            ud.setup_localpath(self.d)

            if bb.data.expand(self.localpath, self.d) is None:
                continue

            if ud.lockfile:
                lf = bb.utils.lockfile(ud.lockfile)

            ud.method.unpack(ud, root, self.d)

            if ud.lockfile:
                bb.utils.unlockfile(lf)

from . import cvs
from . import git
from . import local
from . import svn
from . import wget
from . import svk
from . import ssh
from . import perforce
from . import bzr
from . import hg
from . import osc
from . import repo

methods.append(local.Local())
methods.append(wget.Wget())
methods.append(svn.Svn())
methods.append(git.Git())
methods.append(cvs.Cvs())
methods.append(svk.Svk())
methods.append(ssh.SSH())
methods.append(perforce.Perforce())
methods.append(bzr.Bzr())
methods.append(hg.Hg())
methods.append(osc.Osc())
methods.append(repo.Repo())