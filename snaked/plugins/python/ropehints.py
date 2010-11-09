import re

import weakref

import rope.base.oi.soi
import rope.base.pyobjects
import rope.base.pynames
from rope.base import exceptions
from rope.base.pyobjectsdef import PyModule, PyPackage, PyClass

class ReplacedName(rope.base.pynames.PyName):
    def __init__(self, pyobject, pyname):
        self.pyobject = pyobject
        self.pyname = pyname

    def get_object(self):
        return self.pyobject

    def get_definition_location(self):
        return self.pyname.get_definition_location()


def infer_parameter_objects_with_hints(func):
    def inner(pyfunction):
        params_types = func(pyfunction)
        
        try:
            hintdb = pyfunction.pycore.hintdb
        except AttributeError:
            return params_types
        
        param_names = pyfunction.get_param_names(False)
        for i, name in enumerate(param_names):
            ptype = hintdb.get_function_param_type(pyfunction, name)
            if ptype is not None:
                params_types[i] = ptype
        
        return params_types
        
    return inner

rope.base.oi.soi.infer_parameter_objects = infer_parameter_objects_with_hints(
    rope.base.oi.soi.infer_parameter_objects)


def infer_returned_object_with_hints(func):
    def inner(pyfunction, args):
        try:
            hintdb = pyfunction.pycore.hintdb
        except AttributeError:
            return func(pyfunction, args)
        
        rtype = hintdb.get_function_param_type(pyfunction, 'return')
        if rtype is None:
            rtype = func(pyfunction, args)
        
        return rtype
        
    return inner

rope.base.oi.soi.infer_returned_object = infer_returned_object_with_hints(
    rope.base.oi.soi.infer_returned_object)

def get_attribute_scope_path(obj):
    if isinstance(obj, (PyModule, PyPackage)):
        return obj.pycore.modname(obj.resource)
    elif isinstance(obj, (PyClass,)):
        return get_attribute_scope_path(obj.get_module()) + '.' + obj.get_name()
        
def get_attribute_with_hints(func, what):
    def inner(self, name):
        #print what, get_attribute_scope_path(self), name 

        getting_name = 'getting_attr_%s' % name
        if getattr(self, getting_name, False):
            return func(self, name)

        try:
            hintdb = self.pycore.hintdb
        except AttributeError:
            return func(self, name)

        try:
            setattr(self, getting_name, True)
            result = hintdb.get_module_attribute(self, name)
        except exceptions.AttributeNotFoundError:
            result = None
        except Exception:
            raise
        finally:
            setattr(self, getting_name, False)
        
        if result is None:
            return func(self, name)
        
        return result
        
    return inner
        
PyModule.get_attribute = get_attribute_with_hints(PyModule.get_attribute, 'mod')
PyPackage.get_attribute = get_attribute_with_hints(PyPackage.get_attribute, 'pkg')


def get_attributes_with_hints(func):
    def inner(self):
        #print 'request attributes for', get_attribute_scope_path(self) 

        result = func(self)

        try:
            hintdb = self.pycore.hintdb
        except AttributeError:
            return result

        result.update(hintdb.get_class_attributes(self))
        
        return result
    
    return inner

PyClass._get_structural_attributes = get_attributes_with_hints(PyClass._get_structural_attributes)


class HintProvider(object):
    def __init__(self, project):
        self._project = weakref.ref(project)
    
    @property
    def project(self):
        """Return rope project
        
        :rtype: rope.base.project.Project
        """
        return self._project()

    def get_function_param_type(self, pyfunc, name):
        """Should resolve type for function's parameter `name`
        
        Also should resolve return type if name == 'return'
        If there is no any type hints None is returned
        """
        return None

    def get_scope_path(self, scope):
        result = []
        current_scope = scope
        while current_scope is not None:
            pyobj = current_scope.pyobject
            if isinstance(pyobj, PyModule):
                name = pyobj.pycore.modname(pyobj.resource)
            else:
                name = pyobj.get_name()
        
            result.insert(0, name)
            current_scope = current_scope.parent
        
        return '.'.join(result)

    def get_module_attribute(self, pymodule, name):
        """Resolves module/package attribute's PyName"""
        return None

    def get_class_attributes(self, pyclass):
        """Returns additional atributes for pyclass"""
        return {}

    def get_type(self, type_name, scope=None):
        pycore = self.project.pycore
        module, sep, name = type_name.strip('()').rpartition('.')
        if module:
            module = pycore.get_module(module)
            try:
                pyname = module[name]
            except exceptions.AttributeNotFoundError:
                pyname = None
        elif scope:
            pyname = scope.lookup(name)
        else:
            pyname = pycore.get_module(name)
        
        return pyname


class ScopeHintProvider(HintProvider):
    def __init__(self, project, scope_matcher):
        super(ScopeHintProvider, self).__init__(project)
        self.matcher = scope_matcher
        
    def get_function_param_type(self, pyfunc, name):
        scope_path = self.get_scope_path(pyfunc.get_scope())
        type_name = self.matcher.find_param_type_for(scope_path, name)
        if type_name:
            pyname = self.get_type(type_name)
            if pyname:
                return pyname.get_object()
        
        return None

    def get_module_attribute(self, pymodule, name):
        scope_path = get_attribute_scope_path(pymodule)

        type_name = self.matcher.find_attribute_type_for(scope_path, name)
        if type_name:
            type = self.get_type(type_name)
        else:
            type = None
        
        if type:
            if type_name.endswith('()'):
                obj = rope.base.pyobjects.PyObject(type.get_object())
                pyname = ReplacedName(obj, type)
            else:
                pyname = type
        else:
            pyname = None
        
        return pyname

    def get_class_attributes(self, pyclass):
        attrs = {}

        scope_path = get_attribute_scope_path(pyclass)
        for name, type_name in self.matcher.find_class_attributes(scope_path):
            type = self.get_type(type_name)
            if type:
                if type_name.endswith('()'):
                    obj = rope.base.pyobjects.PyObject(type.get_object())
                    attrs[name] = ReplacedName(obj, type)
                else:
                    attrs[name] = type

        return attrs
        

class ReScopeMatcher(object):
    def __init__(self):
        self.attribute_hints = []
        self.param_hints = []
        self.class_attributes = []
        
    def add_attribute_hint(self, scope, name, object_type):
        self.attribute_hints.append((re.compile(scope), re.compile(name), object_type))  

    def add_param_hint(self, scope, name, object_type):
        self.param_hints.append((re.compile(scope), re.compile(name), object_type))  

    def add_class_attribute(self, scope, name, object_type):
        self.class_attributes.append((re.compile(scope), name, object_type))  

    def find_attribute_type_for(self, scope_path, name):
        for scope, vname, otype in self.attribute_hints:
            if scope.match(scope_path) and vname.match(name):
                return otype
                
        return None

    def find_param_type_for(self, scope_path, name):
        for scope, vname, otype in self.param_hints:
            if scope.match(scope_path) and vname.match(name):
                return otype
                
        return None
        
    def find_class_attributes(self, scope_path):
        for scope, vname, otype in self.class_attributes:
            if scope.match(scope_path):
                yield vname, otype


class CompositeHintProvider(HintProvider):
    def __init__(self, project):
        super(CompositeHintProvider, self).__init__(project)
        
        self.hint_provider = []
        
        self.db = ReScopeMatcher()
        self.db.add_param_hint('ropehints\.init$', 'provider$',
            'snaked.plugins.python.ropehints.CompositeHintProvider()')

        self.db.add_param_hint('re\.compile$', 'return', 're.RegexObject()')
        self.db.add_param_hint('re\.search$', 'return', 're.MatchObject()')
        self.db.add_param_hint('re\.match$', 'return', 're.MatchObject()')
        self.db.add_attribute_hint('re$', 'RegexObject$', 'snaked.plugins.python.stub.RegexObject')
        self.db.add_attribute_hint('re$', 'MatchObject$', 'snaked.plugins.python.stub.MatchObject')

        self.add_hint_provider(ScopeHintProvider(project, self.db)) 
        
        from .dochints import DocStringHintProvider
        self.add_hint_provider(DocStringHintProvider(project)) 
    
    def add_hint_provider(self, provider):
        self.hint_provider.insert(0, provider)

    def get_function_param_type(self, pyfunc, name):
        for p in self.hint_provider:
            result = p.get_function_param_type(pyfunc, name)
            if result is not None:
                return result
                
        return None

    def get_module_attribute(self, pymodule, name):
        for p in self.hint_provider:
            try:
                result = p.get_module_attribute(pymodule, name)
                if result is not None:
                    return result
            except AttributeError:
                pass
            
        return None
        
    def get_class_attributes(self, pyclass):
        attrs = {}
        for p in self.hint_provider:
            attrs.update(p.get_class_attributes(pyclass))
        
        return attrs