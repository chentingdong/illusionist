from datetime import datetime
import json
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, func
from sqlalchemy.ext.declarative import as_declarative, DeclarativeMeta, declared_attr
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import make_transient
from python_utils.config import get_config
from python_utils.logger import logger_console as logger
import traceback


db = SQLAlchemy(session_options={
    'autocommit': False,
    'autoflush': False,
    'expire_on_commit': False
})


class JsonDeSerMixin(object):
    def to_json(self):
        result = dict()
        for key in self.__mapper__.c.keys():
            col = getattr(self, key)
            if isinstance(col, datetime):
                col = col.isoformat()
            result[key] = col
        return result

    def from_json(self, json_obj):
        for k, v in json_obj.items():
            setattr(self, k, v)

        db.session.merge(self)
        error = db.session.commit()
        return not error


class AuditMixin(object):
    """
        AuditMixin
        Mixin for models, adds 4 columns to stamp, time and user on creation and modification
        will create the following columns:

        :created on:
        :changed on:
    """
    @declared_attr
    def created_on(self):
        return Column(DateTime, default=datetime.now, nullable=False)

    @declared_attr
    def changed_on(self):
        return Column(DateTime, default=datetime.now,
                        onupdate=datetime.now, nullable=False)


def lazy_property(f):
    internal_property = '__lazy_' + f.__name__

    @property
    def lazy_property_wrapper(self):
        try:
            return getattr(self, internal_property)
        except AttributeError:
            v = f(self)
            setattr(self, internal_property, v)
            return v
        except Exception as e:
            raise e
    return lazy_property_wrapper

class LazyMixin(object):
    def invalidate(self, property_name=None):
        """
        Invalidate a lazy property or all lazy properties in the object. If the lazy property does not exist,
        raise AttributeError.
        :param property_name: a lazy property
        :type property_name: str
        :raise: AttributeError
        """
        if property_name is None:
            lazy_properties = (name for name in dir(self) if name.startswith('__lazy_'))
            for p in lazy_properties:
                delattr(self, p)
        else:
            lazy_property_name = '__lazy_' + property_name
            if hasattr(self, lazy_property_name):
                delattr(self, lazy_property_name)


class register_parameter(object):
    PARAMETER_REGISTRY = '__parameter_defaults__'

    def __init__(self, section='main', name='', default='', parameter_type=None, description='', target_class=None):
        self.section = section
        self.name = name
        self.default = default
        if parameter_type is None:
            self.parameter_type = type(default)
        else:
            self.parameter_type = parameter_type
        self.description = description
        self.target_class = target_class

    def __call__(self, cls):
        if self.target_class is not None:
            tcls = self.target_class
        else:
            tcls = cls
        section = cls.__name__ + ':' + self.section
        field = '{}{}'.format(self.PARAMETER_REGISTRY, tcls.__name__)

        if not hasattr(tcls, field):
            # TODO:  decide whether should use dir(cls) or dir(tcls)
            parent_fields = [field for field in dir(cls) if field.startswith(register_parameter.PARAMETER_REGISTRY)]
            parent_param_names = set()
            parent_params = []
            for f in parent_fields:
                p_params=getattr(cls, f)
                # use append then sum should be faster if the loop is deep.  Otherwise, extend should be fast.
                parent_params.append([p for p in p_params if p['name'] not in parent_param_names])
                parent_param_names|={p['name'] for p in p_params}
            parent_params=sum(parent_params, [])

            setattr(tcls, field, parent_params)
        params = getattr(tcls, field)
        found = [p for p in params if p['name'] == self.name]
        if found:
            msg = 'Parameter {} already defined in class {}'.format(self.name, found[0]['cls'])
            logger.error(msg)
            logger.debug(traceback.format_exc())
            raise ValueError(msg)
        params.append({'section': section,
                       'name': self.name,
                       'default': self.default,
                       'type': self.parameter_type,
                       'description': self.description,
                       'cls': tcls.__name__})
        return cls


class ParametrizedMixin(LazyMixin):
    # requires mysql 5.7
    @declared_attr
    def params(self):
        return Column(MutableDict.as_mutable(JSON), default={})

    def __init__(self, params={}):
        self.params = params

    @lazy_property
    def parameters(self):
        if not self.params or self.params == JSON.NULL:
            return {}

        try:
            return self.params
        except:
            msg = 'Parameters {} can not be parsed as json'.format(self.params)
            logger.error(msg)
            logger.debug(traceback.format_exc())
            raise ValueError(msg)

    @classmethod
    def registered_parameters(cls):
        field = '{}{}'.format(register_parameter.PARAMETER_REGISTRY, cls.__name__)
        if not hasattr(cls, field):
            parent_fields = [field for field in dir(cls) if field.startswith(register_parameter.PARAMETER_REGISTRY)]
            parent_param_names = set()
            parent_params = sum([[p for p in getattr(cls, f) if p['name'] not in parent_param_names]
                                for f in parent_fields], [])
            setattr(cls, field, parent_params)
        return getattr(cls, field)

    @classmethod
    def parameter_default(cls, name):
        """
        Get the default value for a parameter
        :param name: the name of the parameter
        :type name: str
        :return: the default value registered
        """
        for p in cls.registered_parameters():
            if p['name'] == name:
                return p['default']
        return None

    @classmethod
    def parameter_type(cls, name):
        """
        Get the type for a parameter
        :param name: the name of the parameter
        :type name: str
        :return: the type registered
        """
        for p in cls.registered_parameters():
            if p['name'] == name:
                return p['type']
        return None

    @classmethod
    def df_registered_parameters(cls, section=None):
        from pandas import DataFrame
        df = DataFrame(cls.registered_parameters())
        if section is None:
            return df
        else:
            return df.query('section=="{}"'.format(section))

    @classmethod
    def json_registered_parameters(cls, section=None):
        from pandas import DataFrame
        df = DataFrame(cls.registered_parameters())
        if df.empty:
            return '[]'

        df = df[['section', 'name', 'description', 'default']]
        if section is None:
            return df.to_json(orient='records')
        else:
            return df.query('section=="{}"'.format(section)).to_json(orient='records')

    @classmethod
    def markup_registered_parameters(cls, section=None):
        rp = cls.registered_parameters()
        if rp is None or len(rp) == 0:
            return "[]"

        html = "<table width=90%><tr><th>Section</th><th>Name</th><th>Default</th><th>Description</th></tr>"
        for row in rp:
            if section is None or row['section'] == section:
                html += "<tr><td>{}&nbsp;&nbsp;&nbsp;</td><td>{}&nbsp;&nbsp;&nbsp;</td><td>{}&nbsp;&nbsp;&nbsp;</td>" \
                        "<td>{}&nbsp;&nbsp;&nbsp;</td></tr>"\
                    .format(row['section'], row['name'], row['default'], row['description'])
        html += "</table>"
        return html

    def get(self, name, default=None):
        """
        Get the value for a parameter by name
        :param name: the name of the parameter
        :type name: str
        :param default: a given overridden default value
        :return: the value
        """
        return self.parameters.get(name, default or self.parameter_default(name))

    def set(self, name, value):
        """
        Set the value for a parameter
        :param name: the name of the parameter
        :type name: str
        :param value: the value to set
        :return: self
        """
        registered_type = self.parameter_type(name)
        if registered_type is None:
            msg = 'Parameter {} is not registered for class {}'.format(name, self.__class__.__name__)
            logger.error(msg)
            logger.debug(traceback.format_exc())
            raise ValueError(msg)
        if not isinstance(value, registered_type) and not isinstance(registered_type, type(None)):
            msg = 'The value {} is not the same type as registered {}'.format(value, registered_type)
            logger.error(msg)
            logger.debug(traceback.format_exc())
            raise ValueError(msg)
        self.parameters[name] = value
        self.invalidate(name)
        self.params = self.parameters
        return self


class Versions(db.Model):
    """
    Versions to keep track of all record versions

    need to manually create this table because python_utils doens't have any db configured.

    CREATE TABLE `versions` (
      `id` int(11) NOT NULL AUTO_INCREMENT,
      `class_name` varchar(256) NOT NULL,
      `record_name` varchar(256) NOT NULL,
      `max_version` int(11) NOT NULL,
      PRIMARY KEY (`id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=latin1
    """

    tablename = 'versions'
    __tablename__ = 'versions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    class_name = Column(String(256), nullable=False)
    record_name = Column(String(256), nullable=False)
    max_version = Column(Integer, default=0, nullable=False)

    def current_max_version(self):
        self.max_version = Versions.max_version + 1
        db.session.add(self)
        db.session.commit()
        return self.max_version


class VersionedMixin(object):
    """
    Mixin to versioned objects
    """
    __versioned__ = True

    version_delimiter = '@'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(250), nullable=False)
    version = Column(Integer, default=1, nullable=False)

    def __init__(self, name='', version=None):
        self.name = name
        if version is not None:
            if isinstance(version, int):
                self.version = version
            else:
                raise ValueError('Version can only be integer')

    def __repr__(self):
        return VersionedMixin.version_delimiter.join((self.name, str(self.version)))

    def __str__(self):
        return self.__repr__()

    def set_version(self):
        """
        Set the version of this model. We find the largest version in the database for this model name,
        increment on top of that.
        :return:
        """
        tc = self.__class__
        version = db.session.query(Versions).filter(Versions.class_name == tc.__name__,
                                                 Versions.record_name == self.name).first()
        if not version:
            version = Versions()
            version.class_name = tc.__name__
            version.record_name = self.name
            _, v = db.session.query(tc, func.max(tc.version)).filter(tc.name == self.name).first()
            version.max_version = v if v else 0
            db.session.add(version)
            db.session.commit()

        self.version = version.current_max_version()

    def new_version(self, **kwargs):
        self.set_version()
        make_transient(self)
        self.id = None
        for key, value in kwargs.items():
            if hasattr(self, key) and value is not None:
                setattr(self, key, value)
        return self
