'''
'''
import codecs
import json
import markdown
import os
from ordereddict import OrderedDict
import re
import requests
import time
import traceback
import yaml
import sys
import time
import traceback

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler 
except:
    Observer = None
    LoggingEventHandler = object

from snakecharmer.propertized import Propertized, Prop
from snakecharmer.script_options import ScriptOptions, Opt
 
def main():
    options = Options.from_argv()
    is_interactive_mode = False
    if not options.hub_id:
        is_interactive_mode = True
        handle_interactive_mode(options)
    if not os.path.isdir(options.target_folder):
        print "The target folder (%s) does not exist" % options.target_folder
        sys.exit(1)
    if not os.path.isdir(options.target_folder + "/files") and not os.path.isdir(options.target_folder + "/templates. Exiting."):
        print "You have neither a 'files' folder or a 'templates' folder in the target folder (%s).  There is nothing to sync.  Exiting." % options.target_folder
        sys.exit(1)
        
    if options.action == 'sync':
        sync_folder(options)
    elif options.action == 'watch':
        watch_folder(options)
    else:
        options.print_help()

    if is_interactive_mode:
        print "Quitting..."
        time.sleep(7)

def handle_interactive_mode(options):
    target_folder = raw_input("What folder do you want to sync? (Leave blank to use current folder \"%s\"): " % options.target_folder)
    if target_folder.strip():
        options.target_folder = target_folder.strip()
    if not os.path.isdir(options.target_folder):
        fatal("The target folder (%s) does not exist." % options.target_folder)

    config_path = options.target_folder + "/.cos-sync-config.yaml"
    config = {}
    if os.path.isfile(config_path):
        f = open(config_path, 'r')
        config = yaml.load(f) or {}
        f.close()
        options.hub_id = config.get('hub_id')
        options.api_key = config.get('api_key')
    id_msg = ''
    if options.hub_id:
        id_msg = " (leave blank for default of %s)" % options.hub_id
    options.hub_id = raw_input("Enter your portal_id/hubid%s: " % id_msg).strip() or options.hub_id
    if not str(options.hub_id).isdigit():
        fatal("That is not a valid hubid")
    key_msg = "Get a key at https://app.hubspot.com/keys/get"
    if options.api_key:
        key_msg = 'Leave blank for default of "%s"' % options.api_key
    options.api_key = raw_input("Enter your API key (%s): " % key_msg).strip() or options.api_key
    if not options.api_key:
        fatal("That is not a valid API key")
    options.action = "watch"

    if options.hub_id != config.get('hub_id') or options.api_key != config.get('api_key'):
        remember = raw_input("Remember the hubid and api_key for next time? (Y/yes or no)?: " )
        if remember.lower() in ('y', 'yes'):
            config['hub_id'] = options.hub_id
            config['api_key'] = options.api_key
            f = open(config_path, 'w')
            yaml.dump(config, f)
            f.close()

    print "Syncing then watching folder " + options.target_folder

def fatal(msg):
    print msg
    print "Exiting..."
    time.sleep(7)     
    sys.exit(1)                 

def sync_folder(options):
    file_details = crawl_directory_and_load_file_details(options.target_folder)
    syncer = Syncer(options)
    for file_details in file_details:
        syncer.sync_if_changed(file_details)
 
def watch_folder(options):
    sync_folder(options)
    event_handler = FileSyncEventHandler(options)
    observer = Observer()
    observer.schedule(event_handler, path=options.target_folder, recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()   

cos_types = ['files', 'templates', 'scripts', 'styles', 'pages', 'site-maps', 'blog-posts']

class FileSyncEventHandler(FileSystemEventHandler):
    def __init__(self, options):
        self.syncer = Syncer(options)
        self.options = options

    def on_modified(self, event):
        try:
            self.do_on_modified(event)
        except:
            traceback.print_exc()

    def do_on_modified(self, event):
        if event.is_directory:
            return
        if '/.sync-history.json' in event.src_path:
            return
        print 'EVENT ', event, event.key 
        self.syncer.handle_file_changed(event.src_path)

def crawl_directory_and_load_file_details(folder):
    all_file_details = []
    for cos_type in cos_types:
        type_folder = folder + '/' + cos_type
        for dir_path, dir_names, file_names in os.walk(type_folder):
            if dir_path.startswith('.'):
                continue
            for file_name in file_names:
                if file_name.endswith('~') or '.#' in file_name or file_name.endswith('#') or file_name.startswith('.'):
                    continue
                print 'FILE NAME ', file_name
                relative_path = dir_path.replace(type_folder, '').strip('/') + '/' + file_name
                relative_path = relative_path.strip('/')
                full_path = dir_path + '/' + file_name
                details = FileDetails().load_from_file_path(full_path, relative_path, cos_type)
                all_file_details.append(details)
    return all_file_details

class Syncer(object):
    def __init__(self, options):
        self.options = options 
        self.sync_history = self._read_sync_history()
        # Rate limit to average of 20 updates per minute

    def handle_file_changed(self, full_path):
        relative_path = full_path.replace(self.options.target_folder, '').strip('/')
        print 'RELATIVE PATH ', relative_path
        cos_type = relative_path.split('/')[0]
        if cos_type not in cos_types:
            return
        relative_path = '/'.join(relative_path.split('/')[1:])
        if relative_path.startswith('.'):
            return
        details = FileDetails().load_from_file_path(full_path, relative_path, cos_type)
        self.sync_file_details(details)

    def _get_last_synced_at(self, file_details):
        return self.sync_history.get(file_details.cos_type + '/' + file_details.relative_path, {}).get('last_sync_at', 0)

    def _get_last_size(self, file_details):
        return self.sync_history.get(file_details.cos_type + '/' + file_details.relative_path, {}).get('last_size', 0)
    
    def _get_object_id(self, file_details):
        return self.sync_history.get(file_details.cos_type + '/' + file_details.relative_path, {}).get('object_id', None) 

    def sync_if_changed(self, file_details):
        if file_details.last_modified_at > self._get_last_synced_at(file_details) and file_details.size != self._get_last_size(file_details):
            self.sync_file_details(file_details)

    def sync_file_details(self, file_details):
        uploader_cls = cos_types_to_uploader[file_details.cos_type]
        uploader = uploader_cls(
            file_details=file_details,
            options=self.options,
            object_id=self._get_object_id(file_details),
            )
        object_id = uploader.upload()
        self._update_sync_history(file_details.cos_type + '/' + file_details.relative_path, object_id, file_details.size)
        self._save_sync_history()

    def _update_sync_history(self, path, object_id, size):
        self.sync_history[path] = {'id': object_id, 'last_sync_at': int(time.time() * 1000), 'last_size': size}
        

    def _save_sync_history(self):
        f = open(self.options.target_folder + '/.sync-history.json', 'w')
        history = OrderedDict(sorted(self.sync_history.items(), key=lambda t: t[0]))
        json.dump(history, f, indent=4)
        f.close()
        
    def _read_sync_history(self):
        if not os.path.isfile(self.options.target_folder + '/.sync-history.json'):
            return {}
        f = open(self.options.target_folder + '/.sync-history.json', 'r')
        try:
            result = json.load(f)
        except:
            traceback.print_exc()
            result = {}
        return result
            


class Options(ScriptOptions):
    action = Opt(choices=['watch', 'sync'])
    target_folder = Opt(default=os.getcwd())
    hub_id = Opt()
    api_key = Opt()
    use_buffer = Opt()

class FileDetails(Propertized):
    last_modified_at = Prop(0)
    relative_path = Prop('')
    full_local_path = Prop('')
    metadata = Prop(dict)
    content = Prop('')
    cos_type = Prop('')
    is_text_file = Prop(False)
    extension = Prop('')
    size = Prop(0)

    text_file_extensions = ['.css', '.txt', '.md', '.html', '.js', '.json', '.yaml']

    @classmethod
    def load_from_file_path(cls, file_path, relative_path, cos_type):
        stat = os.stat(file_path)
        details = cls(
            relative_path=relative_path, 
            full_local_path=file_path,
            cos_type=cos_type,
            extension=os.path.splitext(file_path)[1],
            is_text_file=os.path.splitext(file_path)[1] in cls.text_file_extensions,
            last_modified_at=int(stat.st_mtime * 1000),
            size=stat.st_size
        )
        details._hydrate_content_and_metadata()
        return details

    _html_comment_re = re.compile(r"\[hubspot-metadata\]-->([\w\W]*?)<!--\[end-hubspot-metadata\]")
    _js_comment_re = re.compile(r"\[hubspot-metadata\]([\w\W]*?)\[end-hubspot-metadata\]")
    def _hydrate_content_and_metadata(self):
        if not self.is_text_file:
            return
        f = codecs.open(self.full_local_path, 'r', 'utf-8')
        self.content = f.read()
        f.close()

        m = self._html_comment_re.search(self.content)
        if m:
            try:
                self.metadata = json.loads(m.group(1))
            except:
                print 'Error parsing the meta data for ' + self.full_local_path
                traceback.print_exc()
        m = self._js_comment_re.search(self.content)
        if m:
            try:
                meta_json = '\n'.join(m.group(0).split('\n')[1:-1])
                self.metadata = json.loads(meta_json)
            except:
                traceback.print_exc()
        self.content = self._html_comment_re.sub('', self.content)
        self.content = self._js_comment_re.sub('', self.content)

class BaseUploader(Propertized):
    file_details = Prop() 
    options = Prop()
    object_id = Prop()
    endpoint = ''

    def upload(self):
        object_id = self.get_id_from_details()
        data = self.make_json_data()
        if not object_id:
            object_id = self.lookup_id(data)
        print self.file_details.full_local_path
        if not object_id:
            url = self.get_create_url()
            print 'POST URL IS ', url
            r = requests.post(url, data=json.dumps(data))
            print 'RESULT ', r
            if r.status_code > 299:
                print r.content
            return r.json()['id']
        else:
            url = self.get_put_url(object_id)
            print 'PUT URL IS ', url
            r = requests.put(url, data=json.dumps(data))
            print 'RESULT ', r
            return object_id

    def lookup_id(self, data):
        return None

    def make_json_data(self):
        data = {}
        data.update(self.file_details.metadata)
        self.hydrate_json_data(data)
        return data

    def hydrate_json_data(self, data):
        raise Exception("implement me")


    def get_create_url(self):
        return 'https://api.hubapi.com/content/api/v2/%s?hapikey=%s&portalId=%s' % (self.endpoint, self.options.api_key, self.options.hub_id)

    def get_put_url(self, object_id):
        buffer = '/buffer'
        if not self.options.use_buffer:
            buffer = ''
        return 'https://api.hubapi.com/content/api/v2/%s/%s%s?hapikey=%s&portalId=%s' % (self.endpoint, object_id, buffer, self.options.api_key, self.options.hub_id)

    def get_id_from_details(self):
        if self.object_id:
            return self.object_id
        elif self.file_details.metadata.get('id'):
            return self.file_details.metadata.get('id')
        else:
            return None

class TemplateUploader(BaseUploader):
    endpoint = 'templates'

    def lookup_id(self, data):
        url = 'https://api.hubapi.com/content/api/v2/templates?path=%s&hapikey=%s&portalId=%s' % (data['path'], self.options.api_key, self.options.hub_id)
        r = requests.get(url)
        result = r.json()
        if not result.get('objects', []):
            return None
        else:
            return result.get('objects')[0]['id']

    def hydrate_json_data(self, data):
        data['source'] = self.file_details.content

        
class StyleUploader(TemplateUploader):
    pass

class ScriptUploader(TemplateUploader):
    pass

class FileUploader(BaseUploader):
    endpoint = 'files'

    def upload(self):

        files = {'files': open(self.file_details.full_local_path, 'rb')}
        folder, file_name = os.path.split(self.file_details.relative_path)
        data = {
            "file_names": file_name,
            "folder_paths": folder,
            "overwrite": "true"
         }
        print "FILE DATA ", data 
        object_id = self.get_id_from_details()
        if not object_id:
            object_id = self.lookup_id(data)

        if not object_id:
            url = self.get_create_url()
            print 'POST URL IS ', url
            r = requests.post(url, data=data, files=files)
            print "RESULT ", r
            return r.json()['objects'][0]['id']
        else:
            url = self.get_put_url(object_id)
            print 'POST URL IS ', url
            r = requests.post(url, data=data, files=files)
            print 'RESULT ', r
            return object_id
            


    def lookup_id(self, data):
        alt_key = 'hub/%s/%s' % (self.options.hub_id, os.path.splitext(self.file_details.relative_path)[0])
        url = 'https://api.hubapi.com/content/api/v2/files?alt_key=%s&hapikey=%s&portalId=%s' % (alt_key, self.options.api_key, self.options.hub_id)
        r = requests.get(url)
        result = r.json()
        if not result.get('objects', []):
            return None
        else:
            return result.get('objects')[0]['id']

    def hydrate_json_data(self, data):
        pass
    

class BlogPostUploader(BaseUploader):
    def hydrate_json_data(self, data):
        data['post_html'] = self.file_details.content

class PageUploader(BaseUploader):
    endpoint = 'pages'

    def lookup_id(self, data):
        url = 'https://api.hubapi.com/content/api/v2/pages?slug=%s&hapikey=%s&portalId=%s' % (data['slug'], self.options.api_key, self.options.hub_id)
        r = requests.get(url)
        result = r.json()
        if not result.get('objects', []):
            return None
        else:
            return result.get('objects')[0]['id']

    _fix_img_src = re.compile(r'src="([^\"]+)"')
    _fix_anchor_re = re.compile(r'<a\s+name="([^\"]+)"[^>]*>')
    def hydrate_json_data(self, data):
        if 'slug' not in data:
            data['slug'] = os.path.splitext(self.file_details.relative_path)[0]
            data['slug'] = data['slug'].lower().replace(' ', '-').replace('_', '-').replace('--', '-')
            if data['slug'].endswith('/index'):
                data['slug'] = data['slug'][:-6]
            if data['slug'] == 'index':
                data['slug'] = ''
        if 'html_title' not in data:
            data['html_title'] = os.path.split(data['slug'])[1].replace('-', ' ').replace('_', ' ').title()
        if 'name' not in data:
            data['name'] = data['slug'].replace('-', ' ').replace('_', ' ').replace('/', ' > ').title()
            if not data['name']:
                data['name'] = 'Home'
        if 'deleted_at' not in data:
            data['deleted_at'] = 0
        data['widget_containers'] = {}
        data['widgets'] = {}
        if self.file_details.content.find('[start-widget') < 100:
            self._hydrate_widgets_via_brackets(data)
        else:
            self._hydrate_widgets_via_pyquery(data) 


    def _hydrate_widgets_via_pyquery(self, data):
        dom = pq('<div id="pyquery">' + self.file_details.content + '</div>')
        for div in dom("#pyquery > div"):
            if div.attrib.get('container_name'):
                container = {'widgets': []}
                data['widget_containers'][div.attrib['container_name']] = container
                for widget_div in div.getchildren():
                    widget = {'body': {}, 'type': widget_div.get('widget_type')}
                    container['widgets'].append(widget)
                    for attr_div in widget_div.getchildren():
                        html = pq(attr_div).html()
                        html = self._clean_html(html)
                        widget['body'][attr_div.get('attribute_name')] = html
            elif div.attrib.get('widget_name'):
                widget = {'body': {}}
                data['widgets'][div.attrib['widget_name']] = widget
                for attr_div in div.getchildren():
                    html = pq(attr_div).html()
                    html = self._clean_html(html)
                    widget['body'][attr_div.get('attribute_name')] = html
        
    _attr_re = re.compile(r'(\w+)=\"([^"]*)\"')
    def _hydrate_widgets_via_brackets(self, data):
        html = self.file_details.content
        attribute_lines = None
        current_attribute_name = None
        is_markdown = None
        container = None
        widget = None
        for line in html.split('\n'):
            attr_data = dict(self._attr_re.findall(line))
            if line.strip().startswith('[start-container'):
                container = {'widgets': []}
                data['widget_containers'][attr_data['name']] = container
            elif line.strip().startswith('[start-widget'):
                if container:
                    widget = {'type': attr_data['type'], 'body': {}}
                    container['widgets'].append(widget)
                else:
                    widget = {'body': {}}
                    data['widgets'][attr_data['name']] = widget
            elif line.strip().startswith('[start-attribute'):
                attribute_lines = []
                is_markdown = attr_data.get('is_markdown', '').lower() == 'true'
                current_attribute_name = attr_data['name']
            elif line.strip().startswith('[end-attribute]'):
                attr_html = '\n'.join(attribute_lines)
                if is_markdown:
                    attr_html = markdown.markdown(attr_html, ['fenced_code', 'toc']) 
                    attr_html = attr_html.replace('&amp;lbrace;', '&#123;')
                widget['body'][current_attribute_name] = attr_html
                attribute_lines = None
                current_attribute_name = None
                is_markdown = None
            elif line.strip().startswith('[end-widget]'):
                widget = None
            elif line.strip().startswith('[end-container]'):
                container = None
            elif attribute_lines != None:
                attribute_lines.append(line)
        
            

    def _clean_html(self, html):
        html = html.replace('{{', '&#123;&#123;')
        html = self._fix_anchor_re.sub(r'<a name="\g<1>"></a>', html)
        def replacer(match):
            link = match.group(1)
            if not '//' in link and not link.startswith('/'):
                link = 'http://cdn2.hubspot.net/hub/%s/%s' % (self.options.hub_id, link)
            print 'LINK REPLACER ', link
            return 'src="%s"' % link
        html = self._fix_img_src.sub(replacer, html)
        return html


class SiteMapUploader(BaseUploader):
    endpoint = 'site-maps'

    def lookup_id(self, data):
        name = os.path.splitext(self.file_details.relative_path)[0]
        url = 'https://api.hubapi.com/content/api/v2/site-maps?name=%s&hapikey=%s&portalId=%s' % (name, self.options.api_key, self.options.hub_id)
        r = requests.get(
            url
            )
        result = r.json()
        if not result.get('objects', []):
            return None
        else:
            return result.get('objects')[0]['id']
        
    def hydrate_json_data(self, data):
        # load all pages or slug__in?
        # get the page ids
        pages_tree = yaml.load(self.file_details.content)
        data['pages_tree'] = {
            'children': pages_tree
            }
        self._hydrate_page_ids(data['pages_tree'])

    def _hydrate_page_ids(self, tree):
        slug_to_node = {}
        all_slugs = []
        def build_dicts(node):
            slug = node.get('url')
            if slug and '//' not in slug:
                if slug.startswith('/'):
                    slug = slug[1:]
                    slug_to_node[slug] = node
                    all_slugs.append(slug)
            for child_node in node.get('children', []):
                build_dicts(child_node)
        build_dicts(tree)
        slugs_in = '&'.join(['slug__in=%s' % slug for slug in all_slugs])
        url = 'https://api.hubapi.com/content/api/v2/pages?%s&hapikey=%s&portalId=%s' % (slugs_in, self.options.api_key, self.options.hub_id)
        r = requests.get(url)
        for page in r.json().get('objects', []):
            slug_to_node[page['slug']]['page_id'] = page['id']
        
    

cos_types_to_uploader = {
    'styles': StyleUploader,
    'templates': TemplateUploader,
    'scripts': ScriptUploader,
    'files': FileUploader,
    'blog-posts': BlogPostUploader,
    'pages': PageUploader,
    'site-maps': SiteMapUploader
}            

if __name__ == '__main__':
    main()
        
