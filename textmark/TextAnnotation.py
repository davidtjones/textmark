import math
from abc import ABC, abstractmethod
from collections import defaultdict
from queue import Queue

import numpy as np
from shapely.geometry import Polygon


class Graph:
    def __init__(self):
        self.graph = defaultdict(list)

    def add_edge(self, source, target):
        self.graph[source].append(target)

    def __getitem__(self, name):
        return self.graph[name]

# TODO: Create a new subclass "PointAnnotation" that uses points and gets data
#       for point-based methods. We may eventually want a PixelAnnotation for
#       segmentation that has its own get_data
#       
#       e.g., 
#       class PointAnnotation(TextAnnotation, ABC)
#       and
#       class PixelAnnotation(TextAnnotation, ABC)     

class TextAnnotation(ABC):
    _type = "AbstractGeneric"  # be sure to override this in your subclass
    _conversion_registry = {}
    _name_registry = {}
    _conversion_graph = Graph()

    @classmethod
    def register_conversion(cls, source_class, target_class, func):
        cls._conversion_registry[(source_class, target_class)] = func
        cls._conversion_graph.add_edge(source_class, target_class)

    @classmethod
    def register_name(cls, name, class_name):
        cls._name_registry[name] = class_name

    @classmethod
    def factory(cls, name, text, language, *args):
        if name in cls._name_registry:
            return cls._name_registry[name](text, language, *args)
        else:
            raise KeyError(
                f"Invalid name '{name}' not recognized by factory. "
                f"Should be one of {list(cls._name_registry.keys())}"
            )
    
    # TODO: Optionally, there may be parameters associated with a given 
    #       converstion. E.g., if we convert from a bezier curve to a polygon,
    #       the user may wish for more or less points to be sampled. It would
    #       be nice if we could optionally extract the path itself and then
    #       provide function arguments to the functions we wish to parameterize.
    
    # TODO: it seems like we hit an infinite loop if a conversion isn't found
    #       e.g., poly -> bezier
    def to(self, target_class):
        """
        Returns a new TextAnnotation object of the given target class type.

        `target_class` can either be a string referencing the target class or
        the class type (e.g., BoxAnnotation).

        The target class must registered in both the name and conversion
        registries.
        """
        if isinstance(target_class, str):
            # if target_class is a string, convert it to the correct class
            if target_class in self._name_registry:
                target_class = self._name_registry[target_class]
            else:
                raise KeyError(
                    f"Annotation name {target_class} is invalid for conversion. "
                    f"Should be one of {list(self._name_registry.keys())}"
                )

        if issubclass(target_class, TextAnnotation):
            conversion_path = self._find_conversion_path(type(self), target_class)
            current_instance = self

            if type(current_instance) != target_class:
                for intermediate_class in conversion_path:
                    conversion_func = self._conversion_registry[
                        (type(current_instance), intermediate_class)
                    ]
                    current_instance = conversion_func(current_instance)
            return current_instance
        else:
            raise TypeError(
                "Expected subclass of TextAnnotation or string name for conversion"
            )
            
    @classmethod
    def _find_conversion_path(cls, start_class, target_class):
        # use breadth first search to find path from source to target
        q = Queue()
        q.put([start_class])
        while not q.empty():
            curr_path = q.get()
            if curr_path[-1] == target_class:
                return curr_path[1:]
            for e in cls._conversion_graph[curr_path[-1]]:
                q.put(curr_path + [e])

    @classmethod
    def from_serialized(cls, serialized):
        """Factory method"""

        ant_type = serialized.pop("type")
        text = serialized.pop("text")
        language = serialized.pop("language")
        points = list(serialized.values())

        return cls.factory(ant_type, text, language, *points)

    def get_data(self):
        """
        This method should return a JSON-serializable dictionary for the
        annotation, including information about the annotation's text, language,
        and construction.

        The classes in this library are created with the construction

            x1, y1, x2, y2, ... xn, yn

        This is flexible enough to be used for (almost?) any annotation type, is
        easily serialized, and is easily parsed without needing special rules.
        Note that DotAnnotation, which uses only one point, still returns xy
        data as x1, y1. This allows downstream application to use consistent
        parsing techniques for all types. It is strongy recommended that new
        derived classes follow this schema.
        """
        out_dict = {
            "type": type(self)._type,
            "text": self.text,
            "language": self.language,
        }

        if self.points:
            for idx, val in enumerate(self.points):
                out_dict[f"x{idx+1}"], out_dict[f"y{idx+1}"] = val
            return out_dict

    def copy(self):
        """
        If your subclass does not follow the schema above, you will need to
        override this class!
        """
        data = self.get_data()
        new_pts = []
        for i in range(1, ((len(data) - 3) // 2) + 1):
            new_pts.append(data[f"x{i}"])
            new_pts.append(data[f"y{i}"])

        text = data["text"]
        lang = data["language"]
        return type(self)(text, lang, *new_pts)

    def __repr__(self):
        return f"{type(self)._type}({self.text})"

    def __init__(self, text, language=None, *args):
        self.text = text
        self.language = language
        self.points = None


class DotAnnotation(TextAnnotation):
    _type = "Dot"

    def __init__(self, text, language, *args: list[int | float]):
        super().__init__(text=text, language=language)
        if len(args) != 2:
            raise ValueError("Two int values are required")
        x, y = args
        self.points = [(x, y)]

    def to_box(self):
        """Creates a "box" around the dot"""
        x, y = self.points[0]
        return BoxAnnotation(
            self.text, self.language, x - 1, y + 1, x + 1, y - 1
        )


class BoxAnnotation(TextAnnotation):
    """
    Standard 2D Box.

    Box Annotations should be in the form [left, top, right, bottom]. This
    ordering is enforced. This is done to make representations consistent and
    predictable, which is crucial for downstream applications. If this is
    undesirable, extend this class and override the _fix_args_order
    function.
    """

    _type = "Box"

    def __init__(self, text, language, *args: list[int | float]):
        super().__init__(text=text, language=language)
        if len(args) != 4:
            raise ValueError("Four int values are required")

        args = self._fix_args_order(args)

        self.points = list(zip(args[::2], args[1::2]))

    def _fix_args_order(self, args):
        # points must go from upper left to lower right, but we're in screen
        # coordinates, dy should be positive
        dx = args[2] - args[0]
        dy = args[3] - args[1]

        if dx == 0 or dy == 0:
            raise ValueError("Not a valid box")

        elif dx > 0 and dy > 0:
            # this is correct
            pass
        elif dx < 0 and dy > 0:
            #  points are backwards
            args = [args[2], args[3], args[0], args[1]]
        elif dx > 0 and dy < 0:
            # upside down
            args = [args[0], args[3], args[2], args[1]]
        elif dx < 0 and dy < 0:
            # points are backwards and need the other corners
            args = [args[2], args[1], args[0], args[3]]
        return args

    def to_dot(self):
        """Returns the centerpoint of the 2D Box"""
        x1, y1 = self.points[0]
        x2, y2 = self.points[1]

        # Midpoint formula
        x = (x1 + x2) // 2
        y = (y1 + y2) // 2
        return DotAnnotation(self.text, self.language, x, y)

    def to_quad(self):
        """
        Adds the missing two points to make a quad. The order below ensures this
        quad can be drawn as as polygon without overlapping on itself.
        """
        x1, y1 = self.points[0]
        x2, y2 = self.points[1]

        return QuadAnnotation(
            self.text,
            self.language,
            x1,
            y1,
            x1,
            y2,
            x2,
            y2,
            x2,
            y1,
        )


class QuadAnnotation(TextAnnotation):
    """
    Standard 2D Quadrilateral.

    Like Box, QuadAnnotation enforces point order. If this is undesirable,
    extend this class and override _fix_args_order.
    """

    _type = "Quad"

    def __init__(self, text: str, language, *args: list[int | float]):
        super().__init__(text=text, language=language)
        if len(args) != 8:
            raise ValueError("Eight numeric values are required")

        args = self._fix_args_order(args)

        self.points = list(zip(args[::2], args[1::2]))

    def _fix_args_order(self, args):
        """
        Sorts points in "clockwise" order starting from top left.
        """
        points = list(zip(args[::2], args[1::2]))

        points.sort(key=lambda p: (p[0], -p[1]))
        top_left = points[0] if points[0][1] < points[1][1] else points[1]
        bottom_left = points[0] if points[0][1] > points[1][1] else points[1]

        points.sort(key=lambda p: (-p[0], p[1]))
        top_right = points[0] if points[0][1] < points[1][1] else points[1]
        bottom_right = points[0] if points[0][1] > points[1][1] else points[1]

        points = [top_left, top_right, bottom_right, bottom_left]

        args = [coord for point in points for coord in point]
        return args

    def to_box(self):
        polygon = Polygon(self.points)
        bounding_box = polygon.bounds

        # Shapely uses math coordinates
        minx, miny, maxx, maxy = bounding_box
        bounding_box = [minx, maxy, maxx, miny]
        return BoxAnnotation(self.text, self.language, *bounding_box)

    def to_polygon(self):
        # TODO: see: parameteriziation in TextAnnotation.to method. The user may
        #       wish to sample n points from the quad to build the polygon.
        return PolygonAnnotation(self.text, self.language, *self.points)


class PolygonAnnotation(TextAnnotation):
    _type = "Poly"

    def __init__(self, text: str, language, *args: list[int | float]):
        super().__init__(text=text, language=language)

        if len(args) % 2 != 0:
            raise ValueError("An even number of values are required!")

        self.points = list(zip(args[::2], args[1::2]))

    def to_quad(self):
        """
        Quads are actually kind of hard. I'm unsure if it is possible to
        reliably ever convert to a quad because the shape and position are so
        ambiguous. I can ensure that quads are always drawn the same way
        relative to the image axes, but if text is rotated 90 degrees and the
        quad is constructed relative to the angle of the text, there may be a
        problem. I'd like to test this more and investigate if datasets do this.
        """
        # This may not be a guaranteed solution for very odd shapes.
        poly = Polygon(self.points)

        convex_hull = poly.convex_hull

        min_rect = convex_hull.minimum_rotated_rectangle
        ext_coords = list(min_rect.exterior.coords)[:-1]  # drop duplicate

        centroid = poly.centroid.coords[0]

        def angle_with_centroid(point):
            return math.atan2(point[1] - centroid[1], point[0] - centroid[0])

        # Sort vertices based on angle
        sorted_vertices = sorted(ext_coords, key=angle_with_centroid)

        flattened_points = [int(coord) for point in sorted_vertices for coord in point]

        return QuadAnnotation(self.text, self.language, *flattened_points)


class BezierCurveAnnotation(TextAnnotation):
    # Potentially more compact than polygon but can represent more (?) shapes
    _type = "Bezier"

    def __init__(self, text: str, language, *args):
        """
        args: a 1x16 matrix defining two bezier curves. Each pair of
        elements defines a coordinate-pair, e.g.

        [x1, y1, ... x8, y8, x9, y9, ... x16, y16]

        where the first and last pairs of each interior array is an endpoint,
        and the second and third pairs are control points. The first bezier
        curve should be located spatially above the second curve.

        Note: currently, no safeguards are implemented to prevent the bezier
        curve from going "outside" the bounds of the image (the image dims are
        not even part of TextAnnotation's spec). When using annotations of this
        type, you should implement some kind of safeguard if you later convert
        to another type, e.g., bounding boxes:

        np.clip(my_bezier.get_data()
        """
        super().__init__(text=text, language=language)
        if len(args) != 16:
            raise ValueError("16 numerical (int/float) values are required!")
        self.points = list(zip(args[::2], args[1::2]))
        self.curves = [self.points[0:4], self.points[4:8]]

    @staticmethod
    def _bezier_fn(curve, t):
        """
        Calculate coordinate of a point in the bezier curve.
        Curve is a [4, 2] list of coordinate pairs. `t` is the parameter.

        Returns the x, y coordinate pair at point t along the curve.
        """
        curve = np.array(curve)
        bernstein = np.array(
            [(1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t**2, t**3]
        )
        point = np.sum(bernstein[:, np.newaxis] * curve, axis=0)
        return point.tolist()

    def to_polygon(self):
        n = 16  # TODO: see: parameterization in TextAnnotation.to method
        
        curve_top = np.array(self.points[:4]).reshape(4, 2)
        curve_bottom = np.array(self.points[4:]).reshape(4, 2)

        t = np.linspace(0, 1, n)
        t = t.reshape(-1, 1)

        # Setup bezier function
        bernstein = np.array(
            [(1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t**2, t**3]
        )
        bernstein = bernstein.transpose(2, 1, 0)

        # Compute points on the curve
        points_top = bernstein @ curve_top
        points_bottom = bernstein @ curve_bottom

        polygon_full = np.concatenate(
            (points_top.reshape(-1), points_bottom.reshape(-1)), axis=0
        )

        return PolygonAnnotation(self.text, self.language, *polygon_full.tolist())


# Register names
for cls in [
    DotAnnotation,
    BoxAnnotation,
    QuadAnnotation,
    PolygonAnnotation,
    BezierCurveAnnotation,
]:
    TextAnnotation.register_name(cls._type, cls)

# Register conversions
conversions = [
    (DotAnnotation, BoxAnnotation, DotAnnotation.to_box),
    (BoxAnnotation, DotAnnotation, BoxAnnotation.to_dot),
    (BoxAnnotation, QuadAnnotation, BoxAnnotation.to_quad),
    (QuadAnnotation, BoxAnnotation, QuadAnnotation.to_box),
    (QuadAnnotation, PolygonAnnotation, QuadAnnotation.to_polygon),
    (PolygonAnnotation, QuadAnnotation, PolygonAnnotation.to_quad),
    (BezierCurveAnnotation, PolygonAnnotation, BezierCurveAnnotation.to_polygon),
]

for source, target, conv_func in conversions:
    TextAnnotation.register_conversion(source, target, conv_func)
